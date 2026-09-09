"""Тесты настроек заведения.

Главный здесь — AdminCoversModelTests. Продукт разворачивается разным кафе,
и каждое поле настроек кто-то заполняет руками при подключении. Поле, не
попавшее в fieldsets админки, правится только через shell — то есть для
заказчика его нет. Так уже случилось: из 31 поля в админке было 14, включая
ни одного реквизита. Тест сторожит, чтобы это не повторилось молча.
"""
from django.contrib import admin as dj_admin
from django.test import TestCase

from .models import SiteSettings


class AdminCoversModelTests(TestCase):
    def _model_fields(self) -> set[str]:
        return {
            f.name
            for f in SiteSettings._meta.get_fields()
            if getattr(f, "editable", False) and not f.auto_created
        }

    def _admin_fields(self) -> set[str]:
        admin_class = dj_admin.site._registry[SiteSettings]
        names: set[str] = set()
        for _, options in admin_class.fieldsets:
            for entry in options["fields"]:
                names.update((entry,) if isinstance(entry, str) else entry)
        return names

    def test_every_field_is_editable_in_admin(self):
        missing = self._model_fields() - self._admin_fields()
        self.assertEqual(
            missing,
            set(),
            "Поля есть в модели, но не в админке — заказчик их не заполнит: "
            + ", ".join(sorted(missing)),
        )

    def test_admin_has_no_phantom_fields(self):
        """Опечатка в fieldsets роняет всю страницу настроек, а не одно поле."""
        phantom = self._admin_fields() - self._model_fields()
        self.assertEqual(phantom, set(), f"В админке поля, которых нет в модели: {phantom}")


class AcquiringDefaultTests(TestCase):
    def test_new_installation_has_no_online_payment(self):
        """Свежее заведение не должно случайно оказаться с включённой оплатой."""
        self.assertEqual(SiteSettings.load().acquiring, SiteSettings.Acquiring.NONE)
