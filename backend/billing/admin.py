"""Подписки в общей админке — то, что раньше было отдельным пультом."""
from django.contrib import admin
from django.utils.html import format_html

from .models import Client, Payment, Subscription

STATUS_COLORS = {
    Subscription.Status.ACTIVE: "#1a7f37",
    Subscription.Status.EXPIRING: "#b58900",
    Subscription.Status.GRACE: "#d1242f",
    Subscription.Status.BLOCKED: "#6e0b14",
}


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("amount", "months", "paid_at", "comment")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_person", "phone", "points")
    search_fields = ("name", "contact_person", "phone", "email")

    @admin.display(description="Точек")
    def points(self, obj):
        return obj.organizations.count()


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "plan", "paid_until", "status_badge", "is_internal")
    list_filter = ("plan", "is_internal")
    search_fields = ("organization__name", "organization__domain")
    inlines = [PaymentInline]
    fieldsets = (
        (
            None,
            {
                "fields": ("organization", "plan", "paid_until", "grace_days",
                           "is_internal", "notes"),
                "description": "Оплату удобнее отмечать платежом внизу — "
                "«оплачено до» продлится само.",
            },
        ),
    )

    @admin.display(description="Статус")
    def status_badge(self, obj):
        status = obj.status()
        return format_html(
            '<b style="color:{}">{}</b>',
            STATUS_COLORS.get(status, "#000"),
            dict(Subscription.Status.choices).get(status, status),
        )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("paid_at", "subscription", "amount", "months", "comment")
    list_filter = ("paid_at",)
    date_hierarchy = "paid_at"
