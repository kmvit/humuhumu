from django.contrib.auth.models import AbstractUser
from django.db import models

from django.contrib.auth.models import UserManager

from core.tenancy import TenantManager, TenantMixin


class User(AbstractUser, TenantMixin):
    """Пользователь сервиса: клиент, официант, повар, кассир-бармен или админ."""

    class Role(models.TextChoices):
        CLIENT = "client", "Клиент"
        WAITER = "waiter", "Официант"
        COOK = "cook", "Повар"
        BAR = "bar", "Бар"
        WAREHOUSE = "warehouse", "Склад"
        ADMIN = "admin", "Админ"

    # AbstractUser делает логин уникальным глобально — в общей базе это
    # значило бы, что «owner» достаётся первому заведению, а остальным
    # приходится выдумывать. Уникальность переносим в границы заведения
    # (constraints ниже), а вход резолвит заведение по домену — см.
    # users/auth.py.
    username = models.CharField("Логин", max_length=150)

    role = models.CharField(
        "Роль", max_length=16, choices=Role.choices, default=Role.CLIENT
    )
    phone = models.CharField("Телефон", max_length=20, null=True, blank=True)

    #: Вход и createsuperuser: ищут по всей базе, иначе не найдут никого
    #: до того, как заведение определено.
    objects = UserManager()
    #: Прикладной код берёт ЭТОТ менеджер: список сотрудников, поиск гостя
    #: по телефону, состав смены — всё это в границах заведения. С objects
    #: панель владельца показывала сотрудников всех кафе сразу.
    tenant = TenantManager()

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "username"], name="unique_username_per_org"
            ),
            # Телефон тоже был уникален глобально: гость одного кафе не
            # смог бы зарегистрироваться в другом со своим номером.
            models.UniqueConstraint(
                fields=["organization", "phone"],
                condition=~models.Q(phone=None),
                name="unique_phone_per_org",
            ),
        ]

    @property
    def is_staff_role(self) -> bool:
        return self.role in (
            self.Role.WAITER,
            self.Role.COOK,
            self.Role.BAR,
            self.Role.WAREHOUSE,
            self.Role.ADMIN,
        )

    def __str__(self):
        return self.phone or self.username
