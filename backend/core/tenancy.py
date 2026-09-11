"""Тенантность: заведение как явная сущность в данных.

Зачем это здесь, пока заведение одно. Продукт разворачивается по инстансу
на точку (своя база, свой домен) — при такой схеме тенант не нужен. Но
когда точек станут десятки, обслуживать столько отдельных баз станет
дорого, и захочется общей. Переписывать тогда придётся каждый запрос, и
любая забытая фильтрация — это чужая выручка на чужом экране.

Поэтому тенантность вводится заранее и в два шага:

- **сейчас**: у всех сущностей есть ссылка на Organization, все запросы
  идут через фильтрующий менеджер, но в каждой базе организация ровно
  одна. Ошибиться фильтром не во что — соседей физически нет;
- **потом**: в базе появляется вторая организация, и код к этому уже
  готов — меняется только то, как current_organization() её определяет
  (по домену запроса или членству пользователя вместо «она одна»).

Кто НЕ наследует TenantModel и почему — см. core.tests.TenancyGuardTests:
там список исключений с обоснованием, и тест следит, чтобы новая модель
не появилась в обход тенанта молча.
"""
from __future__ import annotations

from django.apps import apps
from django.db import models

#: Кэш «единственной организации» на процесс. Пока организация одна на
#: базу, ходить за ней в базу на каждый запрос незачем. Когда тенантов
#: станет много, кэш заменит разбор запроса, и это место уйдёт.
_current: object | None = None


def current_organization():
    """Организация текущего запроса.

    Возвращает None, если организации ещё нет: это бывает на пустой базе
    между миграциями. В этом случае менеджер не фильтрует — иначе
    migrate и loaddata на чистой установке падали бы.
    """
    global _current
    if _current is not None:
        return _current
    Organization = apps.get_model("core", "Organization")
    try:
        _current = Organization.objects.order_by("pk").first()
    except Exception:
        # таблицы ещё нет (первый migrate) — тенанта тоже нет
        return None
    return _current


def reset_organization_cache() -> None:
    """Сбросить кэш. Нужен тестам и команде, меняющей организацию."""
    global _current
    _current = None


class TenantQuerySet(models.QuerySet):
    """bulk_create в обход save() — самая тихая дыра в тенантности.

    save() привязку проставляет, а bulk_create зовёт SQL напрямую, и
    запись ушла бы без заведения (теперь — с ошибкой БД). Чинить это в
    каждом месте вызова значит однажды забыть, поэтому чиним здесь.
    """

    def bulk_create(self, objs, *args, **kwargs):
        org = current_organization()
        if org is not None:
            for obj in objs:
                if obj.organization_id is None:
                    obj.organization = org
        return super().bulk_create(objs, *args, **kwargs)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Менеджер, который сам ограничивает выборку текущим заведением.

    Смысл в том, чтобы фильтрация была по умолчанию, а не по памяти
    разработчика: забыть `.filter(organization=...)` в одном отчёте —
    и заведение увидит чужие данные.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        org = current_organization()
        return qs.filter(organization=org) if org is not None else qs


class TenantMixin(models.Model):
    """Только ссылка на заведение, без подмены менеджера.

    Для моделей, чей менеджер трогать нельзя: User (на нём держится вход
    и createsuperuser) и singleton-настройки, которые грузятся по pk=1.
    """

    organization = models.ForeignKey(
        "core.Organization",
        on_delete=models.CASCADE,
        # related_name не нужен: 24 модели дали бы 24 обратные связи с
        # организации, а ходить оттуда вниз мы не собираемся.
        related_name="+",
        verbose_name="Заведение",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            org = current_organization()
            if org is not None:
                self.organization = org
                # save(update_fields=[...]) не запишет поле, которого нет
                # в списке: добавляем, иначе привязка молча потеряется.
                fields = kwargs.get("update_fields")
                if fields is not None and "organization" not in fields:
                    kwargs["update_fields"] = list(fields) + ["organization"]
        super().save(*args, **kwargs)


class TenantModel(TenantMixin):
    """Ссылка на заведение + фильтрация по нему по умолчанию."""

    objects = TenantManager()
    #: Escape hatch: выборка без фильтра. Нужна поддержке «Падачи» и
    #: обслуживающим командам — но не обычному коду приложения.
    all_objects = models.Manager()

    class Meta:
        abstract = True
        # Django берёт _base_manager для подгрузки связанных объектов и
        # каскадов. Он обязан быть нефильтрующим, иначе удаление или
        # obj.related полезут через тенант-фильтр в неожиданный момент.
        base_manager_name = "all_objects"
