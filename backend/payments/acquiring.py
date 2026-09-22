"""Интернет-эквайринг: гость платит картой онлайн, без терминала.

Не путать с payments/providers.py — там оплата через кассу-терминал
(мы толкаем сумму на устройство). Здесь другое: создаём платёж у банка,
получаем ссылку, гость платит на его странице, результат приходит
уведомлением.

Провайдер выбирается настройкой заведения (SiteSettings.acquiring), а
доступы лежат в отдельной тенантной модели AcquiringCredentials —
зашифрованные и без единого сериализатора. Рядом с настройками сайта им
не место: GET /api/site/ отдаётся без авторизации, и ключ эквайринга
уедет в публичный JSON в тот день, когда кто-то добавит поле в список.

Раньше доступы брались из переменных окружения. На отдельной установке
(одно кафе — свой сервер, свой .env) это работало, но в общей установке
окружение одно на все заведения: ключи второго кафе класть некуда, а
первое, выбрав тот же банк, принимало бы деньги в чужой магазин.
Поэтому окружение осталось запасным источником и действует ТОЛЬКО там,
где заведение в базе одно (см. _env_allowed). Перенос уже прописанных
ключей — команда migrate_acquiring_keys.

Результат оплаты, чем бы он ни пришёл, применяется одной функцией
services.apply_payment_result — она провайдеро-независима.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)

#: Сколько ждём банк. Гость стоит у кассы и смотрит в телефон — вечно
#: висеть нельзя, лучше честная ошибка и оплата наличными.
TIMEOUT = 15.0


class AcquiringError(Exception):
    """Банк отказал или не ответил. Текст показываем сотруднику, не гостю."""


@dataclass(frozen=True)
class Result:
    """Что удалось понять из уведомления или опроса статуса."""

    external_id: str
    success: bool
    #: Платёж ещё в работе (гость не закрыл страницу оплаты). Такие
    #: уведомления игнорируем: заказ трогать рано.
    pending: bool = False
    fiscal_receipt: str = ""


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _env_allowed() -> bool:
    """Можно ли брать доступы из окружения.

    Только на отдельной установке, где заведение в базе одно. В общей
    установке окружение общее, и запасной источник означал бы: второе
    кафе выбирает тот же банк и начинает принимать деньги в магазин
    первого. Пусть лучше честное «нет доступов».
    """
    from core.models import Organization

    try:
        return Organization.objects.count() <= 1
    except Exception:
        # таблиц ещё нет (первый migrate) — установка заведомо одна
        return True


@dataclass(frozen=True)
class Field:
    """Одно поле доступов: что спросить у владельца и как это назвать.

    Драйвер описывает свои поля сам — по этому описанию и рисуется форма
    в панели владельца, и переносятся старые ключи из окружения. Иначе
    каждый новый банк правился бы в трёх местах.
    """

    key: str
    label: str
    #: Переменная окружения со старым значением — для отдельных установок
    #: и разового переноса.
    env: str
    hint: str = ""
    #: Секрет наружу не показываем даже владельцу: отдаём только «задан».
    secret: bool = True
    required: bool = True
    #: На что похоже правильное значение. Нужно не для красоты: в поле
    #: ЮKassa однажды уехали доступы от другого банка, и первым это
    #: увидел гость у кассы — банк отказал уже на его платеже.
    pattern: str = ""
    #: Что сказать владельцу, если значение на это не похоже.
    error: str = ""


def _kopecks(amount: Decimal) -> int:
    """Банки принимают сумму в копейках целым числом."""
    return int((Decimal(amount) * 100).quantize(Decimal("1")))


def _rubles(amount: Decimal) -> str:
    """ЮKassa — исключение: ей сумма нужна строкой в рублях, «240.00»."""
    return str(Decimal(amount).quantize(Decimal("0.01")))


def _description(payment) -> str:
    """Назначение платежа — то, что гость увидит в выписке."""
    return f"Заказ №{payment.order_id}" if payment.order_id else "Оплата"


class BaseAcquirer:
    """Контракт провайдера онлайн-оплаты."""

    name = "base"
    title = "—"
    #: Какие доступы нужны этому банку (см. Field).
    fields: tuple[Field, ...] = ()

    def __init__(self, credentials: dict | None = None) -> None:
        self._credentials = credentials or {}
        self._env_ok: bool | None = None

    def value(self, key: str) -> str:
        """Значение доступа: сперва из доступов заведения, потом окружение."""
        stored = str(self._credentials.get(key, "")).strip()
        if stored:
            return stored
        field = next((f for f in self.fields if f.key == key), None)
        if field is None:
            return ""
        if self._env_ok is None:
            self._env_ok = _env_allowed()
        return _env(field.env) if self._env_ok else ""

    def configured(self) -> bool:
        """Заданы ли доступы. Без них кнопку оплаты показывать нельзя."""
        return bool(self.fields) and all(
            self.value(f.key) for f in self.fields if f.required
        )

    def filled(self) -> dict[str, bool]:
        """Какие поля заданы — для панели владельца (без самих значений)."""
        return {f.key: bool(self.value(f.key)) for f in self.fields}

    def require_configured(self) -> None:
        """Отказ до похода в банк, с адресом, где это чинится.

        Текст видит сотрудник у кассы, а не гость: ему нужно понять, что
        дело не в госте и не в его карте.
        """
        if not self.configured():
            raise AcquiringError(
                f"Не заданы доступы к банку ({self.title}). Их вводит владелец "
                "в панели, раздел «Оплата картой»."
            )

    def create(self, payment, *, return_url: str) -> str:
        """Создать платёж у банка и вернуть ссылку для гостя.

        Обязан проставить payment.external_id — по нему потом находим
        платёж, когда придёт уведомление.
        """
        raise NotImplementedError

    def read_callback(self, payload: dict, headers: dict) -> Result | None:
        """Разобрать уведомление банка. None — уведомление чужое или подделка."""
        raise NotImplementedError

    def status(self, external_id: str) -> Result | None:
        """Спросить банк, что с платежом. None — банк спрашивать не умеем.

        Нужна не только вебхуку. Уведомление может не прийти вовсе —
        например, в кабинете банка не прописан адрес, — и тогда опрос
        остаётся единственным способом узнать, что гость заплатил.
        """
        return None

    def check(self) -> None:
        """Проверить доступы у банка. Молча — значит приняты.

        Проверять надо в момент, когда владелец их вводит: иначе первым
        неверный ключ находит гость, уже собравший заказ. Банк, которому
        такого запроса не задать, проверку пропускает — врать «всё
        хорошо» тут нельзя, но и запрещать подключение не за что.
        """
        self.require_configured()
        self.check_format()

    def check_format(self) -> None:
        """Похожи ли значения на то, что выдаёт этот банк.

        Проверка грубая и нужна лишь там, где у банка не спросишь: она
        ловит ровно ту ошибку, что уже случилась, — ключи от другого
        банка, введённые в чужую форму.
        """
        for f in self.fields:
            value = self.value(f.key)
            if not value or not f.pattern:
                continue
            if not re.fullmatch(f.pattern, value):
                raise AcquiringError(
                    f"{self.title}: поле «{f.label}» заполнено не тем значением."
                    + (f" {f.error}" if f.error else "")
                )


class NoAcquirer(BaseAcquirer):
    """Онлайн-оплаты нет: заведение принимает только наличные и карту на месте."""

    name = "none"
    title = "Нет онлайн-оплаты"

    def create(self, payment, *, return_url: str) -> str:
        raise AcquiringError("Онлайн-оплата у заведения не подключена")

    def read_callback(self, payload: dict, headers: dict) -> Result | None:
        return None

    def check(self) -> None:
        return None


class TBankAcquirer(BaseAcquirer):
    """Т-Касса (Т-Банк).

    Подпись у Т-Кассы одна и та же в обе стороны: значения полей верхнего
    уровня сортируются по имени ключа, к ним добавляется пароль терминала,
    всё склеивается и берётся SHA-256. Поэтому одна функция _token()
    и подписывает наш запрос, и проверяет их уведомление.
    """

    name = "tbank"
    title = "Т-Банк (Т-Касса)"
    api = "https://securepay.tinkoff.ru/v2"
    fields = (
        Field("terminal_key", "Ключ терминала", env="TBANK_TERMINAL_KEY",
              hint="Из личного кабинета Т-Кассы, раздел «Терминалы»", secret=False),
        Field("password", "Пароль терминала", env="TBANK_PASSWORD"),
    )

    @property
    def terminal(self) -> str:
        return self.value("terminal_key")

    @property
    def password(self) -> str:
        return self.value("password")

    def _token(self, data: dict) -> str:
        # В подпись идут только простые поля: вложенные объекты (Receipt,
        # DATA) и сам Token исключаются — так описано у банка.
        parts = {
            k: v for k, v in data.items()
            if k != "Token" and not isinstance(v, (dict, list))
        }
        parts["Password"] = self.password
        joined = "".join(str(parts[k]) for k in sorted(parts))
        return hashlib.sha256(joined.encode()).hexdigest()

    def create(self, payment, *, return_url: str) -> str:
        self.require_configured()

        body = {
            "TerminalKey": self.terminal,
            "Amount": _kopecks(payment.amount),
            # Номер заказа у банка должен быть уникальным. Берём id платежа,
            # а не заказа: по одному заказу может быть вторая попытка оплаты
            # после отказа, и повтор OrderId банк отклонит.
            "OrderId": str(payment.pk),
            "Description": _description(payment),
            "SuccessURL": return_url,
            "FailURL": return_url,
        }
        body["Token"] = self._token(body)

        data = self._post(f"{self.api}/Init", body)
        if not data.get("Success"):
            raise AcquiringError(
                data.get("Message") or data.get("Details") or "Банк отклонил платёж"
            )

        payment.external_id = str(data["PaymentId"])
        payment.save(update_fields=["external_id", "updated_at"])
        return data["PaymentURL"]

    def read_callback(self, payload: dict, headers: dict) -> Result | None:
        got = str(payload.get("Token", ""))
        if not got or got != self._token(payload):
            logger.warning("Т-Касса: подпись уведомления не сошлась")
            return None
        if str(payload.get("TerminalKey")) != self.terminal:
            logger.warning("Т-Касса: уведомление с чужого терминала")
            return None

        status = str(payload.get("Status", ""))
        return Result(
            external_id=str(payload.get("PaymentId", "")),
            # AUTHORIZED — деньги захолдированы, но не списаны. Заказ
            # оплаченным считаем только после CONFIRMED.
            success=status == "CONFIRMED",
            pending=status in ("NEW", "FORM_SHOWED", "AUTHORIZING", "AUTHORIZED"),
        )

    def _post(self, url: str, body: dict) -> dict:
        try:
            response = httpx.post(url, json=body, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise AcquiringError(f"Т-Касса недоступна: {e}") from e


class SberAcquirer(BaseAcquirer):
    """Сбербанк. Этим же шлюзом идёт эквайринг, подключённый через Эвотор.

    Уведомлению от Сбера намеренно НЕ доверяем: подпись у шлюза настраивается
    по-разному (симметричная, на открытом ключе, иногда выключена вовсе), и
    разбирать все варианты — способ однажды принять подделку. Уведомление
    служит только сигналом «сходи посмотри», а настоящий статус мы
    спрашиваем у банка сами. Тот же метод закрывает и потерянное уведомление.
    """

    name = "sber"
    title = "Сбербанк (в т.ч. через Эвотор)"
    api = "https://securepayments.sberbank.ru/payment/rest"

    #: Статусы шлюза: 2 — списано, 6 — авторизация отменена, 3 — возврат.
    PAID = 2

    fields = (
        Field("username", "Логин API-пользователя", env="SBER_USERNAME",
              hint="Обычно вида имя-api", secret=False),
        Field("password", "Пароль API-пользователя", env="SBER_PASSWORD"),
        # Тестовый контур живёт на другом хосте; чтобы не пересобирать
        # образ ради обкатки, адрес можно переопределить.
        Field("api_url", "Адрес шлюза", env="SBER_API_URL",
              hint="Только для тестового контура, в бою пусто",
              secret=False, required=False),
    )

    @property
    def username(self) -> str:
        return self.value("username")

    @property
    def password(self) -> str:
        return self.value("password")

    @property
    def api_url(self) -> str:
        return self.value("api_url") or self.api

    def _auth(self) -> dict:
        return {"userName": self.username, "password": self.password}

    def create(self, payment, *, return_url: str) -> str:
        self.require_configured()

        data = self._post(f"{self.api_url}/register.do", {
            **self._auth(),
            # orderNumber должен быть уникальным у банка — как и у Т-Кассы,
            # берём id платежа, а не заказа (вторая попытка оплаты).
            "orderNumber": str(payment.pk),
            "amount": _kopecks(payment.amount),
            "returnUrl": return_url,
            "failUrl": return_url,
            "description": _description(payment),
        })
        if data.get("errorCode") and str(data["errorCode"]) != "0":
            raise AcquiringError(data.get("errorMessage") or "Банк отклонил платёж")

        payment.external_id = str(data["orderId"])
        payment.save(update_fields=["external_id", "updated_at"])
        return data["formUrl"]

    def read_callback(self, payload: dict, headers: dict) -> Result | None:
        # Из уведомления берём только номер платежа — всё остальное
        # спрашиваем у банка (см. докстроку класса).
        external_id = str(payload.get("mdOrder") or payload.get("orderId") or "")
        if not external_id:
            return None
        return self.status(external_id)

    def status(self, external_id: str) -> Result | None:
        data = self._post(f"{self.api_url}/getOrderStatusExtended.do", {
            **self._auth(),
            "orderId": external_id,
        })
        if data.get("errorCode") and str(data["errorCode"]) not in ("0",):
            logger.warning("Сбер: не смогли узнать статус — %s", data.get("errorMessage"))
            return None

        status = data.get("orderStatus")
        return Result(
            external_id=external_id,
            success=status == self.PAID,
            # Ещё не оплачен и не отменён — гость на странице банка.
            pending=status in (0, 1),
        )

    def _post(self, url: str, body: dict) -> dict:
        try:
            # Шлюз ждёт форму, а не JSON.
            response = httpx.post(url, data=body, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise AcquiringError(f"Сбербанк недоступен: {e}") from e


class YooKassaAcquirer(BaseAcquirer):
    """ЮKassa (принадлежит Сберу; для мелкого бизнеса он ведёт именно сюда).

    От двух других драйверов отличается тремя вещами.

    Сумма — строкой в рублях («240.00»), а не целым числом копеек.

    Повторный запрос защищён ключом идемпотентности, и ключ мы берём не
    случайный, а выведенный из id платежа. Если банк успел создать платёж,
    а ответ до нас не дошёл, повтор вернёт тот же платёж, а не выставит
    гостю второй счёт на ту же сумму.

    Уведомления ЮKassa не подписывает — проверять подлинность нечем.
    Поэтому поступаем как со Сбером: из уведомления берём только номер
    платежа, а статус спрашиваем у банка сами.

    Чек по 54-ФЗ здесь НЕ формируется: для него нужен блок receipt с
    позициями, ставкой НДС, системой налогообложения и контактом гостя —
    ничего этого мы пока не храним. Если в кабинете ЮKassa включены чеки,
    платёж без receipt банк отклонит: сначала заводим эти данные.
    """

    name = "yookassa"
    title = "ЮKassa"
    api = "https://api.yookassa.ru/v3"

    fields = (
        Field("shop_id", "shopId", env="YOOKASSA_SHOP_ID",
              hint="Номер магазина из кабинета ЮKassa, раздел «Ключи API» — только цифры",
              secret=False,
              pattern=r"\d{4,12}",
              error="shopId — это число рядом с названием магазина."),
        Field("secret_key", "Секретный ключ", env="YOOKASSA_SECRET_KEY",
              hint="Боевой ключ вида live_...; тестовый с настоящей картой не сработает",
              pattern=r"(live|test)_[A-Za-z0-9_\-]{10,}",
              error="Ключ ЮKassa начинается с live_ (или test_ для проверок) "
                    "и выдаётся один раз при выпуске."),
    )

    @property
    def shop_id(self) -> str:
        return self.value("shop_id")

    @property
    def secret(self) -> str:
        return self.value("secret_key")

    def _key(self, payment) -> str:
        """Ключ идемпотентности: один и тот же для повторов одного платежа."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://padacha.ru/payments/{payment.pk}"))

    def create(self, payment, *, return_url: str) -> str:
        self.require_configured()

        data = self._post(f"{self.api}/payments", {
            "amount": {"value": _rubles(payment.amount), "currency": "RUB"},
            # capture — списываем сразу, без холда: заказ уже собран,
            # держать деньги и подтверждать вторым запросом незачем.
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": _description(payment),
            # Чтобы платёж в кабинете банка можно было сопоставить с нашим
            # реестром при сверке.
            "metadata": {"payment_id": str(payment.pk)},
        }, idempotence_key=self._key(payment))

        confirmation = data.get("confirmation") or {}
        url = confirmation.get("confirmation_url")
        if not data.get("id") or not url:
            raise AcquiringError("ЮKassa не вернула ссылку на оплату")

        payment.external_id = str(data["id"])
        payment.save(update_fields=["external_id", "updated_at"])
        return url

    def read_callback(self, payload: dict, headers: dict) -> Result | None:
        obj = payload.get("object")
        external_id = str(obj.get("id") or "") if isinstance(obj, dict) else ""
        if not external_id:
            return None
        return self.status(external_id)

    def status(self, external_id: str) -> Result | None:
        """Спросить банк, что с платежом. Ошибку сети наружу не глушим:

        пусть ручка ответит 500 и ЮKassa повторит уведомление — иначе
        оплаченный заказ навсегда останется висеть неоплаченным.
        """
        data = self._get(f"{self.api}/payments/{external_id}")
        status = str(data.get("status", ""))
        return Result(
            external_id=external_id,
            success=status == "succeeded",
            # waiting_for_capture бывает только при capture=false, но пусть
            # будет: деньги захолдированы, а не списаны — заказ не закрываем.
            pending=status in ("pending", "waiting_for_capture"),
        )

    def check(self) -> None:
        """Проверка доступов: спрашиваем банк о самом магазине.

        /me — самый дешёвый авторизованный запрос: денег не двигает,
        платежей не создаёт, а неверный ключ отсекает сразу же.
        """
        self.require_configured()
        self.check_format()
        self._get(f"{self.api}/me")

    def _post(self, url: str, body: dict, *, idempotence_key: str) -> dict:
        try:
            response = httpx.post(
                url,
                json=body,
                timeout=TIMEOUT,
                auth=(self.shop_id, self.secret),
                headers={"Idempotence-Key": idempotence_key},
            )
        except httpx.HTTPError as e:
            raise AcquiringError(f"ЮKassa недоступна: {e}") from e
        return self._read(response)

    def _get(self, url: str) -> dict:
        try:
            response = httpx.get(url, timeout=TIMEOUT, auth=(self.shop_id, self.secret))
        except httpx.HTTPError as e:
            raise AcquiringError(f"ЮKassa недоступна: {e}") from e
        return self._read(response)

    @staticmethod
    def _read(response: httpx.Response) -> dict:
        """Причину отказа ЮKassa пишет в тело ответа, а не в код статуса.

        raise_for_status её бы потерял, и сотрудник увидел бы голое «400»
        вместо «сумма меньше минимальной».
        """
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            raise AcquiringError(
                data.get("description") or f"ЮKassa ответила {response.status_code}"
            )
        return data


_ACQUIRERS = {
    a.name: a
    for a in (NoAcquirer, TBankAcquirer, SberAcquirer, YooKassaAcquirer)
}


def acquirer_class(name: str) -> type[BaseAcquirer]:
    """Класс драйвера по имени. Незнакомое имя — «нет оплаты»."""
    return _ACQUIRERS.get(name, NoAcquirer)


def acquirers() -> list[type[BaseAcquirer]]:
    """Банки, которые умеем, кроме «нет оплаты» — для формы владельца."""
    return [a for a in _ACQUIRERS.values() if a is not NoAcquirer]


def stored_credentials(provider: str) -> dict:
    """Доступы ТЕКУЩЕГО заведения к этому банку. Чужих не отдаст.

    Менеджер модели тенантный, поэтому фильтр по заведению здесь не виден
    и не может быть забыт — он применяется сам (core/tenancy.py).
    """
    from .models import AcquiringCredentials

    try:
        row = AcquiringCredentials.objects.filter(provider=provider).first()
    except Exception:
        # таблицы ещё нет (первый migrate после обновления)
        return {}
    return row.values() if row else {}


def get_acquirer(name: str | None = None) -> BaseAcquirer:
    """Провайдер заведения с его доступами. Без аргумента — из настроек."""
    if name is None:
        from core.models import SiteSettings

        name = SiteSettings.load().acquiring
    cls = acquirer_class(name)
    if cls is NoAcquirer:
        return NoAcquirer()
    return cls(stored_credentials(cls.name))


def online_payment_available() -> bool:
    """Умеет ли заведение принимать оплату онлайн.

    Мало выбрать банк — нужны ещё его доступы, иначе кнопка оплаты
    приведёт гостя к ошибке.
    """
    return get_acquirer().configured()
