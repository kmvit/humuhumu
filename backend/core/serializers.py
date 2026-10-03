from rest_framework import serializers

from .models import SiteSettings


#: Лицо заведения для гостей и банка: название, контакты, реквизиты.
#: Правит только админ — менеджеру по складу и сменам они ни к чему,
#: а ошибка в ИНН ломает оферту и проверку сайта банком.
PROFILE_FIELDS = frozenset({
    "name", "tagline", "app_short_name", "phone", "email", "address",
    "working_hours", "instagram", "telegram", "about",
    "merchant_type", "merchant_name", "merchant_short", "merchant_address",
    "merchant_inn", "merchant_ogrn", "merchant_account", "merchant_bank",
    "merchant_bank_inn", "merchant_bik", "merchant_corr_account",
    "merchant_bank_address", "acquirer", "legal_updated",
})


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
    # Можно ли гостю выбрать «Оплатить на кассе» — заказ уйдёт на кассу
    # заведения. Тоже только «да/нет»: какая касса и её ключ — не для
    # публичной ручки.
    kassa_payment = serializers.SerializerMethodField()
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

    def get_kassa_payment(self, obj) -> bool:
        from payments.providers import kassa_available

        return kassa_available()

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
            "kassa_payment",
            # Ждёт ли заказ гостя оплаты, прежде чем уйти на кухню.
            # Гостю это видно и так по статусу его заказа, а владельцу
            # нужен выключатель в панели.
            "prepay_required",
            # Технический перерыв: гость по этим полям понимает, почему
            # кнопка «Отправить» не работает, а панель владельца их правит.
            "ordering_paused",
            "ordering_pause_note",
            "plan",
            "features",
        )
        # Название, контакты и реквизиты владелец правит сам из панели
        # (раздел «Заведение»): это его данные, не «Падачи». Закрыто только
        # то, что зависит от денег и договора с нами. Менеджеру эти поля
        # не открыты — см. PROFILE_FIELDS и SiteSettingsView.
        read_only_fields = (
            # тариф заведению назначает «Падача», не само заведение
            "plan",
            # формат едет за тарифом и стоит денег: «Стойка» дешевле «Зала».
            # Кнопки в панели нет с самого начала, но поле было открыто —
            # заведение могло включить себе зал обычным PATCH.
            "service_mode",
        )

    # Реквизиты попадают в оферту и на страницу оплаты — банк при проверке
    # сайта сверяет их с выпиской. Опечатку в ИНН проще поймать здесь, чем
    # потом объяснять банку. Пусто — можно: новое заведение заполняет позже.
    # Проверки на уровне полей, а не в validate(): тот не запускается, если
    # упало хоть одно поле, и владелец узнавал бы об ошибках по одной.
    @staticmethod
    def _digits(value: str, lengths: tuple[int, ...], message: str) -> str:
        value = "".join(value.split())  # пробелы из копипаста выписки
        if value and not (value.isdigit() and len(value) in lengths):
            raise serializers.ValidationError(message)
        return value

    def validate_merchant_inn(self, value):
        return self._digits(value, (10, 12), "ИНН — 10 цифр у организации или 12 у ИП")

    def validate_merchant_ogrn(self, value):
        return self._digits(value, (13, 15), "ОГРН — 13 цифр, ОГРНИП — 15")

    def validate_merchant_bank_inn(self, value):
        return self._digits(value, (10,), "ИНН банка — 10 цифр")

    def validate_merchant_bik(self, value):
        return self._digits(value, (9,), "БИК — 9 цифр")

    def validate_merchant_account(self, value):
        return self._digits(value, (20,), "Расчётный счёт — 20 цифр")

    def validate_merchant_corr_account(self, value):
        return self._digits(value, (20,), "Корр. счёт — 20 цифр")

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Название заведения не может быть пустым")
        return value

    def get_logo(self, obj):
        return obj.logo.url if obj.logo else None
