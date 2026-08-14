from django.contrib import admin

from .models import Shift, ShiftMember, ShiftSettings
from .services import shift_report


@admin.register(ShiftSettings)
class ShiftSettingsAdmin(admin.ModelAdmin):
    """Ставка за смену, процент бонуса и штрафной стол."""

    fieldsets = (
        ("Оплата", {"fields": ("daily_rate", "bonus_percent")}),
        ("Списания", {"fields": ("penalty_table",)}),
    )

    def has_add_permission(self, request):
        return not ShiftSettings.objects.exists()  # запись одна

    def has_delete_permission(self, request, obj=None):
        return False


class ShiftMemberInline(admin.TabularInline):
    model = ShiftMember
    extra = 0
    fields = ("user", "role", "added_by", "added_at")
    readonly_fields = ("added_at",)
    autocomplete_fields = ("user", "added_by")


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("date", "members_count", "revenue", "penalty", "payout")
    inlines = [ShiftMemberInline]
    readonly_fields = ("created_at",)
    ordering = ["-date"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("members__user")

    @staticmethod
    def _report(obj):
        # один расчёт на строку списка — колонок из отчёта несколько
        if not hasattr(obj, "_report_cache"):
            obj._report_cache = shift_report(obj)
        return obj._report_cache

    @admin.display(description="В смене")
    def members_count(self, obj):
        return self._report(obj)["members_count"]

    @admin.display(description="Выручка")
    def revenue(self, obj):
        return self._report(obj)["revenue"]

    @admin.display(description="Списания")
    def penalty(self, obj):
        return self._report(obj)["penalty"]

    @admin.display(description="На человека")
    def payout(self, obj):
        return self._report(obj)["payout"]


@admin.register(ShiftMember)
class ShiftMemberAdmin(admin.ModelAdmin):
    list_display = ("shift", "user", "role", "added_by", "added_at")
    list_filter = ("role", "shift__date")
    search_fields = ("user__username", "user__first_name", "user__phone")
    autocomplete_fields = ("user", "added_by")
