from django.contrib import admin

from .models import SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Все настройки заведения — здесь.

    Продукт разворачивается разным кафе, и каждое поле кто-то должен
    заполнить руками при подключении. Поле, которого нет в fieldsets,
    правится только через shell — то есть для заказчика не существует.
    За полнотой следит тест core.tests.AdminCoversModelTests.
    """

    fieldsets = (
        ("Основное", {"fields": ("name", "tagline", "app_short_name", "logo", "about")}),
        (
            "Тариф",
            {
                "fields": ("plan",),
                "description": (
                    "Что включено заведению по тарифной сетке «Падачи». "
                    "Проставляется вручную при подключении; позже сюда "
                    "будет писать лицензия из пульта."
                ),
            },
        ),
        ("Внешний вид", {"fields": ("theme", "accent_color", "dark_by_default")}),
        (
            "Работа заведения",
            {
                "fields": ("service_mode", "item_remove_code"),
                "description": "Формат обслуживания и права официанта на удаление позиций.",
            },
        ),
        ("Контакты", {"fields": ("phone", "email", "address", "working_hours")}),
        ("Соцсети", {"fields": ("instagram", "telegram")}),
        (
            "Приём оплаты",
            {
                "fields": ("acquiring",),
                "description": (
                    "Кто принимает оплату картой онлайн. Ключи и пароли банка "
                    "задаются переменными окружения на сервере заведения "
                    "(TBANK_* или SBER_*), в базе их не храним."
                ),
            },
        ),
        (
            "Реквизиты",
            {
                "fields": (
                    "merchant_type",
                    "merchant_name",
                    "merchant_short",
                    "merchant_address",
                    "merchant_inn",
                    "merchant_ogrn",
                ),
                "description": "Показываются на юридических страницах и в подвале.",
            },
        ),
        (
            "Банковские реквизиты",
            {
                "classes": ("collapse",),
                "fields": (
                    "merchant_account",
                    "merchant_bank",
                    "merchant_bank_inn",
                    "merchant_bik",
                    "merchant_corr_account",
                    "merchant_bank_address",
                ),
            },
        ),
        (
            "Юридические страницы",
            {
                "classes": ("collapse",),
                "fields": ("acquirer", "legal_updated"),
            },
        ),
    )

    def has_add_permission(self, request):
        # запись одна — новую не создаём, если уже есть
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
