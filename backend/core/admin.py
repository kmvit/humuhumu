from django.contrib import admin

from .models import SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Основное", {"fields": ("name", "tagline", "logo", "about")}),
        ("Внешний вид", {"fields": ("theme", "accent_color")}),
        ("Контакты", {"fields": ("phone", "email", "address", "working_hours")}),
        ("Соцсети", {"fields": ("instagram", "telegram")}),
        ("Официанты", {"fields": ("item_remove_code",)}),
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
    )

    def has_add_permission(self, request):
        # запись одна — новую не создаём, если уже есть
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
