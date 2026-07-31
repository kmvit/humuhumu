from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "phone", "role", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "phone", "email")
    fieldsets = UserAdmin.fieldsets + (
        ("Кафе", {"fields": ("role", "phone")}),
    )
