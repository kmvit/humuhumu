from django.contrib import admin

from .models import PayrollPayout


@admin.register(PayrollPayout)
class PayrollPayoutAdmin(admin.ModelAdmin):
    list_display = ("user", "period", "amount", "paid_on", "created_by")
    list_filter = ("period", "paid_on")
    search_fields = ("user__username", "user__first_name", "comment")
    date_hierarchy = "paid_on"
