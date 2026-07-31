from rest_framework import serializers

from .models import TokenPackage, TokenTransaction, Wallet


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ("id", "balance")


class TokenTransactionSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source="get_type_display", read_only=True)

    class Meta:
        model = TokenTransaction
        fields = (
            "id",
            "type",
            "type_display",
            "amount",
            "balance_after",
            "order",
            "comment",
            "created_at",
        )


class TokenPackageSerializer(serializers.ModelSerializer):
    total_tokens = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = TokenPackage
        fields = ("id", "pay_amount", "bonus_amount", "total_tokens", "is_active")


class TopupSerializer(serializers.Serializer):
    """Ручное пополнение клиента кассиром/админом по пакету."""

    client = serializers.IntegerField()
    package = serializers.IntegerField()
