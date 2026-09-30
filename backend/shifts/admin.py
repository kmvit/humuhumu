from django import forms
from django.contrib import admin
from django.utils import timezone

from .models import FocusItem, Shift, ShiftMember, ShiftRate, ShiftSettings, ShiftType
from .services import (
    apply_rules_from,
    clean_kpi_grid,
    clean_kpi_roles,
    retime_type,
    shift_report,
)


def _without_bulk_delete(actions):
    """Массовое удаление обходит проверки has_delete_permission(obj)."""
    actions.pop("delete_selected", None)
    return actions


class ShiftSettingsForm(forms.ModelForm):
    """Те же проверки, что в приложении: сырой JSON тут пишется руками."""

    class Meta:
        model = ShiftSettings
        # Только то, что проверяем. "__all__" строил бы при импорте поля и
        # для внешних ключей (заведение, штрафной стол) — это запрос через
        # тенантный менеджер без выбранного заведения, и на общей базе
        # backend падал при старте. Остальные поля админка добавит сама по
        # fieldsets — уже во время запроса, когда заведение известно.
        fields = ("kpi_grid", "kpi_roles")

    def clean_kpi_grid(self):
        try:
            return clean_kpi_grid(self.cleaned_data["kpi_grid"])
        except ValueError as e:
            raise forms.ValidationError(str(e))

    def clean_kpi_roles(self):
        try:
            return clean_kpi_roles(self.cleaned_data["kpi_roles"] or [])
        except ValueError as e:
            raise forms.ValidationError(str(e))


@admin.register(ShiftSettings)
class ShiftSettingsAdmin(admin.ModelAdmin):
    """Схема оплаты и её параметры. Обычно правится в приложении
    («Смены» → «Оплата»); здесь — то же самое, с теми же последствиями."""

    form = ShiftSettingsForm
    fieldsets = (
        ("Схема", {"fields": ("scheme",)}),
        (
            "Оплата за результат",
            {"fields": ("senior_bonus", "kpi_roles", "kpi_grid")},
        ),
        (
            "Ставка по умолчанию и процент",
            {
                "fields": ("daily_rate", "bonus_percent"),
                "description": (
                    "Ставка — за день при оплате поровну; при оплате за результат — "
                    "для тех, чья клетка «тип смены × роль» не заполнена. Процент "
                    "от выручки работает только при оплате поровну."
                ),
            },
        ),
        ("Списания", {"fields": ("penalty_table",)}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # как и правка в приложении: сегодняшняя и будущие смены подтягивают
        # новые правила, прошлые — нет
        apply_rules_from(timezone.localdate())

    def has_add_permission(self, request):
        return not ShiftSettings.objects.exists()  # запись одна

    def has_delete_permission(self, request, obj=None):
        return False


class ShiftRateInline(admin.TabularInline):
    model = ShiftRate
    extra = 0
    # заведение берётся от типа смены; поле здесь позволило бы привязать
    # ставку к чужому заведению
    fields = ("role", "rate")


@admin.register(ShiftType)
class ShiftTypeAdmin(admin.ModelAdmin):
    """Виды смен и ставки по ролям — то же, что «Смены» → «Оплата»."""

    list_display = ("name", "starts_at", "ends_at", "hours", "sort_order")
    inlines = [ShiftRateInline]

    @admin.display(description="Часов")
    def hours(self, obj):
        return obj.hours

    def save_model(self, request, obj, form, change):
        old = ShiftType.objects.filter(pk=obj.pk).values_list("starts_at", "ends_at").first()
        super().save_model(request, obj, form, change)
        if old:
            retime_type(obj, *old, timezone.localdate())

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        apply_rules_from(timezone.localdate())

    def has_delete_permission(self, request, obj=None):
        # Как в приложении: пока тип стоит в сегодняшней или будущей смене,
        # удаление молча уронило бы людям ставку до общей.
        if obj is not None and obj.members.filter(
            shift__date__gte=timezone.localdate()
        ).exists():
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        return _without_bulk_delete(super().get_actions(request))


class ShiftMemberInline(admin.TabularInline):
    model = ShiftMember
    extra = 0
    fields = (
        "user", "role", "shift_type_name", "starts_at", "ends_at",
        "rate", "is_senior", "in_kpi", "added_by", "added_at",
    )
    # название типа — снимок для истории, его правка ничего не меняет
    readonly_fields = ("shift_type_name", "added_at")
    autocomplete_fields = ("user", "added_by")


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("date", "members_count", "revenue", "penalty", "payout_total")
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

    @admin.display(description="К выплате всего")
    def payout_total(self, obj):
        return self._report(obj)["payout_total"]


@admin.register(ShiftMember)
class ShiftMemberAdmin(admin.ModelAdmin):
    list_display = ("shift", "user", "role", "added_by", "added_at")
    list_filter = ("role", "shift__date")
    search_fields = ("user__username", "user__first_name", "user__phone")
    autocomplete_fields = ("user", "added_by")


@admin.register(FocusItem)
class FocusItemAdmin(admin.ModelAdmin):
    """Фокусные позиции. Правка идущей записи здесь переписала бы прошлые
    выплаты — в приложении правка суммы открывает новую запись."""

    list_display = ("__str__", "starts_at", "ends_at", "created_by")
    list_filter = ("starts_at",)
    autocomplete_fields = ("product", "variant")
    readonly_fields = ("created_at",)

    @staticmethod
    def _started(obj) -> bool:
        return obj is not None and obj.starts_at <= timezone.now()

    def has_change_permission(self, request, obj=None):
        # Начавшаяся запись уже оплачивала продажи: её правка переписала
        # бы их задним числом. Здесь — только просмотр; менять сумму или
        # срок — в приложении («Смены» → «Фокус»), там правка открывает
        # новую запись.
        if self._started(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if self._started(obj):
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        return _without_bulk_delete(super().get_actions(request))
