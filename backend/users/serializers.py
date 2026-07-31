import re

from rest_framework import serializers

from .models import User


class RegisterSerializer(serializers.Serializer):
    """Регистрация клиента: имя и телефон. Пароль выдаём отдельно, при рассылке."""

    name = serializers.CharField(max_length=150, label="Имя")
    phone = serializers.CharField(max_length=20, label="Телефон")

    def validate_phone(self, value):
        digits = re.sub(r"\D", "", value)
        if len(digits) == 11 and digits[0] in "78":
            digits = "7" + digits[1:]
        elif len(digits) == 10:
            digits = "7" + digits
        else:
            raise serializers.ValidationError("Введите номер, например +7 999 000-00-00")
        phone = "+" + digits
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError("Этот номер уже зарегистрирован")
        return phone

    def create(self, validated_data):
        user = User(
            username=validated_data["phone"],
            phone=validated_data["phone"],
            first_name=validated_data["name"].strip(),
            role=User.Role.CLIENT,
        )
        user.set_unusable_password()  # пароль зададим при выдаче доступа
        user.save()
        return user

    def to_representation(self, instance):
        return {"id": instance.id, "name": instance.first_name, "phone": instance.phone}


class MeSerializer(serializers.ModelSerializer):
    """Текущий пользователь + баланс токенов (если есть кошелёк)."""

    balance = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "phone", "role", "balance")

    def get_balance(self, obj):
        wallet = getattr(obj, "wallet", None)
        return wallet.balance if wallet else None
