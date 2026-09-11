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

import contextlib
import contextvars

from django.apps import apps
from django.db import models
from django.utils.deconstruct import deconstructible

#: Заведение текущего запроса. contextvars, а не глобальная переменная:
#: у gunicorn несколько воркеров и потоков, и заведение соседнего запроса
#: не должно протечь в наш.
_current: contextvars.ContextVar = contextvars.ContextVar("organization", default=None)


class NoOrganizationSelected(RuntimeError):
    """Заведений несколько, а какое обслуживаем — не сказано.

    Специально громкая ошибка. Молча вернуть «первое попавшееся» значило
    бы показать одному заведению данные другого; молча вернуть пустоту —
    спрятать баг. Фоновым задачам и командам нужно указать заведение
    явно: `with organization_context(org): ...`
    """


def current_organization():
    """Заведение, которое обслуживаем прямо сейчас.

    Порядок: явно заданное (middleware по домену или organization_context)
    → единственное в базе → ошибка, если их несколько.

    Возвращает None только на пустой базе (между миграциями): тогда
    менеджер не фильтрует, иначе migrate и loaddata не отработали бы.
    """
    org = _current.get()
    if org is not None:
        return org
    Organization = apps.get_model("core", "Organization")
    try:
        first_two = list(Organization.objects.order_by("pk")[:2])
    except Exception:
        # таблиц ещё нет (первый migrate) — заведений тоже нет
        return None
    if not first_two:
        return None
    if len(first_two) > 1:
        raise NoOrganizationSelected(
            "В базе несколько заведений, а текущее не выбрано. Для фоновых "
            "задач и команд используйте organization_context(org)."
        )
    # одно заведение на базу — привычный режим отдельной установки
    return first_two[0]


def current_organization_or_none():
    """current_organization(), но без исключения при неопределённости.

    Для мест, где «заведение не выбрано» — нормальная ситуация, а не
    ошибка: служебный адрес админки, где тенант выбирают переключателем.
    Ограничивать там нечем, и это безопасно: API без заведения не
    обслуживается вовсе (TenantMiddleware отвечает 404).
    """
    try:
        return current_organization()
    except NoOrganizationSelected:
        return None


def set_current_organization(org):
    """Задать заведение запроса и вернуть метку для отката.

    Метку обязательно отдать в reset_current_organization по завершении
    запроса: воркер обслуживает запросы один за другим, и незакрытое
    заведение досталось бы следующему — чужому.
    """
    return _current.set(org)


def reset_current_organization(token) -> None:
    """Вернуть заведение, каким оно было до запроса."""
    _current.reset(token)


@contextlib.contextmanager
def organization_context(org):
    """Выполнить блок от имени заведения — для команд и celery-задач."""
    token = _current.set(org)
    try:
        yield org
    finally:
        _current.reset(token)


@deconstructible
class tenant_upload_to:
    """Путь загрузки внутри папки заведения: org-<id>/<subdir>/<имя файла>.

    В общей установке том media один на всех, и «logo.png» двух кафе
    столкнулись бы. Django-то переименует дубликат, но файлы заведений
    лежали бы вперемешку: не выгрузить и не удалить по-отдельности.

    Класс, а не замыкание: upload_to попадает в миграции, а функцию,
    созданную внутри другой функции, Django сериализовать не умеет.
    """

    def __init__(self, subdir: str):
        self.subdir = subdir

    #: Поля под такие пути объявляются с max_length=200: префикс заведения
    #: удлиняет каждый путь, и стандартных 100 символов перестаёт хватать —
    #: на переезде кафе это обнаружилось скан-чеком ровно в 100 символов.

    def __call__(self, instance, filename: str) -> str:
        org_id = getattr(instance, "organization_id", None) or "common"
        return f"org-{org_id}/{self.subdir}/{filename}"

    def __eq__(self, other):
        # Без этого makemigrations видит «изменение» при каждом запуске.
        return isinstance(other, tenant_upload_to) and other.subdir == self.subdir


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
