from rest_framework import serializers

from .models import SiteSettings


class SiteSettingsSerializer(serializers.ModelSerializer):
    logo = serializers.SerializerMethodField()
    # Тариф и его фичи: фронт по ним прячет разделы, которых нет в тарифе.
    # Настоящая защита — permission-классы на бэке (core/plans.py), фронт
    # только не показывает лишнего.
    features = serializers.SerializerMethodField()
    # Умеет ли заведение принимать оплату картой онлайн. Фронт по этому
    # флагу решает, показывать ли гостю кнопку оплаты. Само название банка
    # и тем более его ключи наружу не отдаём — только «да/нет».
    online_payment = serializers.SerializerMethodField()
    # Для раздела «Оплата» в панели владельца: подключён ли банк и какой.
    # Ключи наружу не отдаём — только факт настроенности и название.
    acquiring_ready = serializers.SerializerMethodField()
    acquiring_name = serializers.SerializerMethodField()
    accent_color = serializers.RegexField(
        regex=r"^#[0-9a-fA-F]{6}$",
        allow_blank=True,
        required=False,
    )

    def get_features(self, obj) -> list[str]:
        from .plans import features

        return sorted(features(obj.plan))

    def get_online_payment(self, obj) -> bool:
        # Провайдера берём из obj, а не через SiteSettings.load(): настройки
        # уже загружены, второй поход в базу на каждый запрос ни к чему.
        # Выключатель владельца поверх доступов: без ключей его «включено»
        # ничего не даёт, гость упёрся бы в ошибку банка.
        from payments.acquiring import get_acquirer

        return obj.online_payment_on and get_acquirer(obj.acquiring).configured()

    def get_acquiring_ready(self, obj) -> bool:
        from payments.acquiring import get_acquirer

        return get_acquirer(obj.acquiring).configured()

    def get_acquiring_name(self, obj) -> str:
        return obj.get_acquiring_display() if obj.acquiring != obj.Acquiring.NONE else ""

    class Meta:
        model = SiteSettings
        fields = (
            "name",
            "tagline",
            "app_short_name",
            "logo",
            "phone",
            "email",
            "address",
            "working_hours",
            "instagram",
            "telegram",
            "about",
            "theme",
            "service_mode",
            "dark_by_default",
            # бонусная программа — админ правит их из раздела «Бонусы»
            "bonus_enabled",
            "bonus_welcome",
            "bonus_earn_percent",
            "bonus_redeem_waiter",
            "bonus_redeem_guest",
            "accent_color",
            "merchant_type",
            "merchant_name",
            "merchant_short",
            "merchant_address",
            "merchant_inn",
            "merchant_ogrn",
            "merchant_account",
            "merchant_bank",
            "merchant_bank_inn",
            "merchant_bik",
            "merchant_corr_account",
            "merchant_bank_address",
            "acquirer",
            "legal_updated",
            "online_payment",
            "online_payment_on",
            "acquiring_ready",
            "acquiring_name",
            "plan",
            "features",
        )
        # через API правится только внешний вид; остальное — в админке
        read_only_fields = (
            "name",
            "tagline",
            "app_short_name",
            "phone",
            "email",
            "address",
            "working_hours",
            "instagram",
            "telegram",
            "about",
            "merchant_type",
            "merchant_name",
            "merchant_short",
            "merchant_address",
            "merchant_inn",
            "merchant_ogrn",
            "merchant_account",
            "merchant_bank",
            "merchant_bank_inn",
            "merchant_bik",
            "merchant_corr_account",
            "merchant_bank_address",
            "acquirer",
            "legal_updated",
            # тариф заведению назначает «Падача», не само заведение
            "plan",
        )

    def get_logo(self, obj):
        return obj.logo.url if obj.logo else None
