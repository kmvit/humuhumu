from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.admin import TenantAdminMixin

from .models import User
from .admin_forms import CafeUserCreationForm, CafeUserChangeForm


@admin.register(User)
class CustomUserAdmin(TenantAdminMixin, UserAdmin):
    add_form = CafeUserCreationForm
    form = CafeUserChangeForm
    readonly_fields = ("selected_cafe",)
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("selected_cafe", "username", "password1", "password2")}),
    )

    @admin.display(description="Заведение")
    def selected_cafe(self, obj=None):
        from core.tenancy import current_organization_or_none
        return current_organization_or_none() or "Сначала выберите заведение"

    list_display = ("username", "phone", "role", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "phone", "email")
    fieldsets = UserAdmin.fieldsets + (
        ("Кафе", {"fields": ("role", "phone", "organization", "is_platform_admin")}),
    )
