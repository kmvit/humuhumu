from django.contrib import admin

from .models import BonusTransaction, LoyaltyMember


@admin.register(LoyaltyMember)
class LoyaltyMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "birth_date", "balance", "created_at")
    search_fields = ("user__first_name", "user__phone")
    readonly_fields = ("balance", "created_at")  # баланс двигают только проводки


@admin.register(BonusTransaction)
class BonusTransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "member", "type", "amount", "balance_after", "order")
    list_filter = ("type",)
    search_fields = ("member__user__phone", "member__user__first_name")
    readonly_fields = ("member", "type", "amount", "balance_after", "order", "comment", "created_at")

    def has_add_permission(self, request):
        # журнал ведут services — вручную проводку не создать
        return False
