from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "purpose", "status", "amount", "provider", "created_at")
    list_filter = ("purpose", "status", "provider")
    search_fields = ("external_id",)
    readonly_fields = ("external_id",)
