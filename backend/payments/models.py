from django.conf import settings
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
        # Возврат — отдельная запись реестра, а не правка исходной:
        # деньги ушли обратно в свой день, и касса должна это видеть.
        REFUND = "refund", "Возврат гостю"

    class Status(models.TextChoices):
        PENDING = "pending", "Создан"
        SUCCEEDED = "succeeded", "Оплачен"
        FAILED = "failed", "Ошибка"
        CANCELLED = "cancelled", "Отменён"
        REFUNDED = "refunded", "Возвращён"

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
    # Что касса сказала об оплате — для возврата через неё же. Без номера
    # слипа касса не поймёт, какой платёж вернуть на карту, а взять его
    # потом негде: в чеке он есть только пока заказ лежит у нас «живым».
    kassa_receipt_id = models.CharField("Чек на кассе (id)", max_length=64, blank=True, default="")
    kassa_slip_id = models.CharField("Слип оплаты картой (id)", max_length=64, blank=True, default="")
    # RRN — номер операции у банка: по нему возврат сверяют с выпиской.
    kassa_rrn = models.CharField("RRN операции", max_length=32, blank=True, default="")
    # Возврат через кассу идёт по шагам (деньги на карту → чек возврата),
    # и каждый шаг — операция на кассе. Здесь — на каком шаге возврат, что
    # вернула касса и почему не вышло: чтобы опрос довёл его, а повтор
    # продолжил с места сбоя, а не вернул деньги на карту второй раз.
    kassa_meta = models.JSONField("Ход операции на кассе", default=dict, blank=True)
    # Касса не подтвердила оплату сама, и сотрудник отметил «Оплачено»
    # руками. Такие платежи владелец сверяет с отчётом кассы: если чек
    # не пробит, заказ так и лежит на кассе в отложенных.
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Подтвердил вручную",
    )
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


class KassaSync(TenantModel):
    """Синхронизация терминала кассы с её облаком — через личный кабинет.

    Терминал aQsi теряет связь с облаком: отложенный заказ лежит в
    кабинете, а на кассу не приходит, пока там не нажмут «Синхронизировать».
    Эту кнопку жмём мы сами (payments/kassa_sync.py). Здесь — сессия
    кабинета, чтобы не входить заново каждый раз, и итог последней
    попытки: его видит владелец, и по нему же держим паузу между
    попытками — общую для всех процессов, а не в памяти одного.
    """

    provider = models.CharField("Касса", max_length=32)
    #: Зашифрованная cookie сессии кабинета (payments/secrets.py).
    session = models.TextField("Сессия кабинета (шифр)", blank=True, default="")
    session_until = models.DateTimeField("Сессия до", null=True, blank=True)
    attempted_at = models.DateTimeField("Последняя попытка", null=True, blank=True)
    synced_at = models.DateTimeField("Последняя удачная", null=True, blank=True)
    error = models.CharField("Ошибка последней попытки", max_length=300, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider"],
                name="uniq_kassa_sync_per_org",
            ),
        ]
        verbose_name = "Синхронизация кассы"
        verbose_name_plural = "Синхронизация касс"

    def __str__(self):
        return f"Синхронизация {self.provider}"
