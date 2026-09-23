from rest_framework import serializers

from .models import SiteSettings


class SiteSettingsSerializer(serializers.ModelSerializer):
    logo = serializers.SerializerMethodField()
    # Тариф и его фичи: фронт по ним прячет разделы, которых нет в тарифе.
    # Настоящая защита — permission-классы на бэке (core/plans.py), фронт
    # только не показывает лишнего.
    features = serializers.SerializerMethodField()
    # Умеет ли заведение принимать оплату картой онлайн. Фронт по этому
    # флагу решает, показывать ли гостю кнопку оплаты. Здесь только
    # «да/нет»: ручка публичная, и название банка с состоянием его
    # доступов — дело владельца, для него есть GET /api/acquiring/.
    # Раньше они отдавались отсюда же и уезжали всем подряд.
    online_payment = serializers.SerializerMethodField()
    accent_color = serializers.RegexField(
        regex=r"^#[0-9a-fA-F]{6}$",
        allow_blank=True,
        required=False,
    )

    # Тариф и фичи отдаём ДЕЙСТВУЮЩИЕ: при общей базе их назначает
    # подписка, а поле настроек — лишь запасной источник. Иначе владелец
    # оплачивал бы «Максимум», а разделы оставались скрытыми.
    plan = serializers.SerializerMethodField()

    def get_plan(self, obj) -> str:
        from .plans import current_plan

        return current_plan()

    def get_features(self, obj) -> list[str]:
        from .plans import features

        return sorted(features())

    def get_online_payment(self, obj) -> bool:
        # Провайдера берём из obj, а не через SiteSettings.load(): настройки
        # уже загружены, второй поход в базу на каждый запрос ни к чему.
        # Выключатель владельца поверх доступов: без ключей его «включено»
        # ничего не даёт, гость упёрся бы в ошибку банка.
        from payments.acquiring import get_acquirer

        return obj.online_payment_on and get_acquirer(obj.acquiring).configured()

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
            # Ждёт ли заказ гостя оплаты, прежде чем уйти на кухню.
            # Гостю это видно и так по статусу его заказа, а владельцу
            # нужен выключатель в панели.
            "prepay_required",
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
            # формат едет за тарифом и стоит денег: «Стойка» дешевле «Зала».
            # Кнопки в панели нет с самого начала, но поле было открыто —
            # заведение могло включить себе зал обычным PATCH.
            "service_mode",
        )

    def get_logo(self, obj):
        return obj.logo.url if obj.logo else None
