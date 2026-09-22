from django.db import models

from core.tenancy import TenantModel


class Payment(TenantModel):
    """Платёж по заказу: наличными на кассе, через терминал или картой онлайн.

    provider говорит, кто его провёл: "manual" — касса, имя эквайера
    (tbank/sber) — онлайн-оплата, имя терминального провайдера — терминал.
    external_id — номер платежа у банка, по нему находим платёж, когда
    придёт уведомление.
    """

    class Purpose(models.TextChoices):
        ORDER = "order", "Оплата заказа"
        TOPUP = "topup", "Пополнение токенов"

    class Status(models.TextChoices):
        PENDING = "pending", "Создан"
        SUCCEEDED = "succeeded", "Оплачен"
        FAILED = "failed", "Ошибка"
        CANCELLED = "cancelled", "Отменён"

    class Method(models.TextChoices):
        CASH = "cash", "Наличные"
        CARD = "card", "Карта"
        QR = "qr", "QR / СБП"

    purpose = models.CharField("Назначение", max_length=16, choices=Purpose.choices)
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.PENDING
    )
    amount = models.DecimalField("Сумма, ₽", max_digits=12, decimal_places=2)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Заказ",
    )
    token_package = models.ForeignKey(
        "wallet.TokenPackage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Пакет токенов",
    )
    method = models.CharField(
        "Способ", max_length=8, choices=Method.choices, blank=True, default=""
    )
    # Умолчание — «наличными на кассе»: платёж без явного провайдера
    # создаётся только там. Прежнее "yookassa" в умолчании врало: платёж
    # у кассы приписывался банку, через который не проходил.
    provider = models.CharField("Провайдер", max_length=32, default="manual")
    # Уникален ВНУТРИ заведения, а не на всю базу: у кафе разные терминалы
    # и разные договоры с банками, номера платежей у них свои и вполне
    # могут совпасть. С глобальной уникальностью второй платёж просто не
    # сохранился бы — деньги пришли, а заказ остался открытым.
    external_id = models.CharField(
        "ID у провайдера", max_length=128, null=True, blank=True
    )
    fiscal_receipt = models.CharField("Фискальный чек", max_length=64, blank=True, default="")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "external_id"],
                condition=~models.Q(external_id=None),
                name="uniq_payment_external_id_per_org",
            ),
        ]
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Платёж №{self.pk} — {self.amount} ₽"


class AcquiringCredentials(TenantModel):
    """Доступы заведения к своему банку: shopId, пароль терминала и прочее.

    Отдельная модель, а не поля в SiteSettings, и это главное в ней. У
    настроек сайта есть публичный сериализатор (GET /api/site/ отдаётся
    без авторизации), и секрет, положенный рядом с названием кафе, уедет
    наружу в тот день, когда кто-нибудь допишет поле в список. Здесь
    сериализатора нет вовсе: чтобы утечь, мало забыть про поле — надо
    завести сериализатор чужой модели целиком.

    Значения лежат одним зашифрованным блобом, а не колонками: у банков
    разный набор полей (у Т-Кассы два, у Сбера три), и каждый новый банк
    иначе требовал бы миграцию. Что внутри — описывает сам драйвер
    (acquiring.Field), он же единственный, кто эти ключи читает.

    Запись — на заведение И на банк: если кафе ушло из Сбера в Т-Банк, а
    через месяц вернулось, старые доступы не должны быть стёрты сменой
    выбора.
    """

    provider = models.CharField("Банк", max_length=32)
    #: Зашифрованный JSON, см. payments/secrets.py. Пусто — доступов нет.
    payload = models.TextField("Доступы (шифр)", blank=True, default="")
    updated_at = models.DateTimeField("Обновлены", auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider"],
                name="uniq_acquiring_credentials_per_org",
            ),
        ]
        verbose_name = "Доступы к банку"
        verbose_name_plural = "Доступы к банкам"

    def __str__(self):
        return f"Доступы {self.provider}"

    def values(self) -> dict:
        from .secrets import decrypt

        return decrypt(self.payload)

    def set_values(self, values: dict) -> None:
        from .secrets import encrypt

        # Пустые значения не храним: пустая строка в блобе означала бы
        # «поле задано», и configured() отвечал бы «да» на пустой пароль.
        clean = {k: v.strip() for k, v in values.items() if isinstance(v, str) and v.strip()}
        self.payload = encrypt(clean)
