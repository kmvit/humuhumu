from django.contrib import admin

from .models import TokenPackage, TokenTransaction, Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance")
    search_fields = ("user__username", "user__phone")
    readonly_fields = ("balance",)  # баланс меняется только проводками, не руками


@admin.register(TokenTransaction)
class TokenTransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "wallet", "type", "amount", "balance_after", "order")
    list_filter = ("type", "created_at")
    search_fields = ("wallet__user__username", "wallet__user__phone")
    readonly_fields = ("balance_after",)


@admin.register(TokenPackage)
class TokenPackageAdmin(admin.ModelAdmin):
    list_display = ("pay_amount", "bonus_amount", "total_tokens", "is_active")
    list_editable = ("bonus_amount", "is_active")
