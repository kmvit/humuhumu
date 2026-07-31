from django.contrib import admin

from .models import SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Основное", {"fields": ("name", "tagline", "logo", "about")}),
        ("Контакты", {"fields": ("phone", "email", "address", "working_hours")}),
        ("Соцсети", {"fields": ("instagram", "telegram")}),
    )

    def has_add_permission(self, request):
        # запись одна — новую не создаём, если уже есть
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
