import re
from decimal import Decimal

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
        fields = ("id", "name", "phone", "birth_date", "balance", "source", "created_at")


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
    consent = serializers.BooleanField(required=False, default=False, label="Согласие")

    def validate_phone(self, value):
        return normalize_phone(value)

    def validate(self, attrs):
        # Гостя на кассе записывает сотрудник, и согласие гостя — на нём:
        # без отметки в базу чужие данные не заносим.
        if self.context.get("staff") and not attrs.get("consent"):
            raise serializers.ValidationError(
                {"consent": "Отметьте, что гость согласен на обработку данных"}
            )
        return attrs

    def create(self, validated_data):
        from .models import LoyaltyMember
        from .services import enroll_by_phone

        staff = self.context.get("staff")
        return enroll_by_phone(
            validated_data["phone"],
            validated_data["name"],
            validated_data.get("birth_date"),
            source=LoyaltyMember.Source.STAFF if staff else LoyaltyMember.Source.GUEST,
            consent=validated_data.get("consent", False),
        )


class AdminMemberSerializer(EnrollSerializer):
    """Менеджер заводит гостя. С остатком из прежней системы — перенос:
    без приветственных, остаток отдельной проводкой."""

    transfer_balance = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=Decimal("0"),
        label="Остаток из прежней системы",
    )

    def create(self, validated_data):
        from .services import transfer_member

        if validated_data.get("transfer_balance") is None:
            return super().create(validated_data)
        return transfer_member(
            validated_data["phone"],
            validated_data["name"],
            validated_data.get("birth_date"),
            validated_data["transfer_balance"],
            consent=validated_data.get("consent", False),
        )
