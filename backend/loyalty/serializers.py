import re

from rest_framework import serializers

from .models import BonusTransaction, LoyaltyMember


def normalize_phone(value: str) -> str:
    """+7XXXXXXXXXX из любого ввода. Телефон — ключ, по которому официант
    находит гостя, поэтому формат должен быть один на всю базу."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        raise serializers.ValidationError("Введите номер, например +7 999 000-00-00")
    return "+" + digits


class MemberSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    phone = serializers.CharField(read_only=True)

    class Meta:
        model = LoyaltyMember
        fields = ("id", "name", "phone", "birth_date", "balance", "created_at")


class BonusTransactionSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source="get_type_display", read_only=True)

    class Meta:
        model = BonusTransaction
        fields = (
            "id", "type", "type_display", "amount",
            "balance_after", "order", "comment", "created_at",
        )


class EnrollSerializer(serializers.Serializer):
    """Регистрация в программе: имя, телефон, дата рождения."""

    name = serializers.CharField(max_length=150, label="Имя")
    phone = serializers.CharField(max_length=20, label="Телефон")
    birth_date = serializers.DateField(required=False, allow_null=True, label="Дата рождения")

    def validate_phone(self, value):
        return normalize_phone(value)

    def create(self, validated_data):
        from .services import enroll_by_phone

        return enroll_by_phone(
            validated_data["phone"],
            validated_data["name"],
            validated_data.get("birth_date"),
        )
