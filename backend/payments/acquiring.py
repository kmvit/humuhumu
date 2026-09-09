"""Интернет-эквайринг: гость платит картой онлайн, без терминала.

Не путать с payments/providers.py — там оплата через кассу-терминал
(мы толкаем сумму на устройство). Здесь другое: создаём платёж у банка,
получаем ссылку, гость платит на его странице, результат приходит
уведомлением.

Провайдер выбирается настройкой заведения (SiteSettings.acquiring), а
доступы берутся ТОЛЬКО из переменных окружения. Так сделано намеренно:
GET /api/site/ отдаётся без авторизации, и ключ эквайринга, положенный
в ту же модель, рано или поздно уедет в публичный JSON — достаточно,
чтобы кто-то добавил поле в сериализатор.

Результат оплаты, чем бы он ни пришёл, применяется одной функцией
services.apply_payment_result — она провайдеро-независима.
"""
from __future__ import annotations

import hashlib
import logging
import os
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

    def configured(self) -> bool:
        """Заданы ли доступы. Без них кнопку оплаты показывать нельзя."""
        return False

    def create(self, payment, *, return_url: str) -> str:
        """Создать платёж у банка и вернуть ссылку для гостя.

        Обязан проставить payment.external_id — по нему потом находим
        платёж, когда придёт уведомление.
        """
        raise NotImplementedError

    def read_callback(self, payload: dict, headers: dict) -> Result | None:
        """Разобрать уведомление банка. None — уведомление чужое или подделка."""
        raise NotImplementedError


class NoAcquirer(BaseAcquirer):
    """Онлайн-оплаты нет: заведение принимает только наличные и карту на месте."""

    name = "none"
    title = "Нет онлайн-оплаты"

    def configured(self) -> bool:
        return False

    def create(self, payment, *, return_url: str) -> str:
        raise AcquiringError("Онлайн-оплата у заведения не подключена")

    def read_callback(self, payload: dict, headers: dict) -> Result | None:
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

    def __init__(self) -> None:
        self.terminal = _env("TBANK_TERMINAL_KEY")
        self.password = _env("TBANK_PASSWORD")

    def configured(self) -> bool:
        return bool(self.terminal and self.password)

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
        if not self.configured():
            raise AcquiringError("Не заданы TBANK_TERMINAL_KEY и TBANK_PASSWORD")

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

    def __init__(self) -> None:
        self.username = _env("SBER_USERNAME")
        self.password = _env("SBER_PASSWORD")
        # Тестовый контур живёт на другом хосте; чтобы не пересобирать образ
        # ради обкатки, адрес можно переопределить переменной.
        self.api = _env("SBER_API_URL") or self.api

    def configured(self) -> bool:
        return bool(self.username and self.password)

    def _auth(self) -> dict:
        return {"userName": self.username, "password": self.password}

    def create(self, payment, *, return_url: str) -> str:
        if not self.configured():
            raise AcquiringError("Не заданы SBER_USERNAME и SBER_PASSWORD")

        data = self._post(f"{self.api}/register.do", {
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
        data = self._post(f"{self.api}/getOrderStatusExtended.do", {
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

    def __init__(self) -> None:
        self.shop_id = _env("YOOKASSA_SHOP_ID")
        self.secret = _env("YOOKASSA_SECRET_KEY")

    def configured(self) -> bool:
        return bool(self.shop_id and self.secret)

    def _key(self, payment) -> str:
        """Ключ идемпотентности: один и тот же для повторов одного платежа."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://padacha.ru/payments/{payment.pk}"))

    def create(self, payment, *, return_url: str) -> str:
        if not self.configured():
            raise AcquiringError("Не заданы YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY")

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


def get_acquirer(name: str | None = None) -> BaseAcquirer:
    """Провайдер заведения. Без аргумента — из настроек заведения."""
    if name is None:
        from core.models import SiteSettings

        name = SiteSettings.load().acquiring
    return _ACQUIRERS.get(name, NoAcquirer)()


def online_payment_available() -> bool:
    """Умеет ли заведение принимать оплату онлайн.

    Мало выбрать провайдера — нужны ещё доступы в переменных окружения,
    иначе кнопка оплаты приведёт гостя к ошибке.
    """
    return get_acquirer().configured()
