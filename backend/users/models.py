from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Пользователь сервиса: клиент, официант, повар, кассир-бармен или админ."""

    class Role(models.TextChoices):
        CLIENT = "client", "Клиент"
        WAITER = "waiter", "Официант"
        COOK = "cook", "Повар"
        BAR = "bar", "Бар"
        WAREHOUSE = "warehouse", "Склад"
        ADMIN = "admin", "Админ"

    role = models.CharField(
        "Роль", max_length=16, choices=Role.choices, default=Role.CLIENT
    )
    phone = models.CharField(
        "Телефон", max_length=20, unique=True, null=True, blank=True
    )

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

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
