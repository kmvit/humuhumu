from django.contrib import admin

from .models import Expense, ExpenseCategory, PayrollPayout


@admin.register(PayrollPayout)
class PayrollPayoutAdmin(admin.ModelAdmin):
    list_display = ("user", "period", "amount", "paid_on", "created_by")
    list_filter = ("period", "paid_on")
    search_fields = ("user__username", "user__first_name", "comment")
    date_hierarchy = "paid_on"


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("date", "category", "amount", "comment", "created_by")
    list_filter = ("category", "date")
    date_hierarchy = "date"
    search_fields = ("comment",)
