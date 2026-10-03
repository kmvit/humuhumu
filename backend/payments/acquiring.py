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
import ssl
import uuid
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import certifi
import httpx

logger = logging.getLogger(__name__)

#: Сколько ждём банк. Гость стоит у кассы и смотрит в телефон — вечно
#: висеть нельзя, лучше честная ошибка и оплата наличными.
TIMEOUT = 15.0


class AcquiringError(Exception):
    """Банк отказал или не ответил.

    Текст — для владельца и сотрудника (возврат, ввод ключей, панель).
    Гостю его не показываем: при переходе к оплате он получает общую
    фразу, а причина уходит в лог и панель (services.start_online_payment).
    """


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
    #: Выбор из списка вместо ввода: ((значение, подпись), ...).
    choices: tuple = ()
    #: Что подставить, если необязательное поле не задано.
    default: str = ""


def _kopecks(amount: Decimal) -> int:
    """Банки принимают сумму в копейках целым числом."""
    return int((Decimal(amount) * 100).quantize(Decimal("1")))


def _rubles(amount: Decimal) -> str:
    """ЮKassa — исключение: ей сумма нужна строкой в рублях, «240.00»."""
    return str(Decimal(amount).quantize(Decimal("0.01")))


def _callback_url(return_url: str, provider: str) -> str:
    """Адрес нашего вебхука на том же домене, что и страница гостя."""
    parts = urlsplit(return_url)
    return f"{parts.scheme}://{parts.netloc}/api/payments/callback/{provider}/"


def _description(payment) -> str:
    """Назначение платежа — то, что гость увидит в выписке."""
    return f"Заказ №{payment.order_id}" if payment.order_id else "Оплата"


class BaseAcquirer:
    """Контракт провайдера онлайн-оплаты."""

    name = "base"
    title = "—"
    #: Какие доступы нужны этому банку (см. Field).
    fields: tuple[Field, ...] = ()
    #: Тело ответа на уведомление, если банку нужно что-то особенное.
    #: Пусто — обычный JSON.
    ack: str = ""
    #: Сами ли мы сообщаем банку адрес уведомлений (с каждым платежом).
    #: Нет — владелец вписывает его в кабинете банка руками.
    sends_callback_url: bool = False

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

        Текст видит владелец (в панели — как последнюю ошибку банка), а
        не гость: ему нужно понять, что дело не в госте и не в его карте.
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

    def refund(self, payment) -> str:
        """Вернуть гостю деньги по уже оплаченному платежу.

        Возвращает номер возврата у банка (для сверки). Возврат всегда
        полный: частичные суммы продукт пока не умеет, и лучше честный
        отказ, чем возврат «примерно на сколько-то».

        Банк, которому такого запроса не задать, обязан сказать это
        прямо: деньги нельзя «вернуть молча», иначе сотрудник решит,
        что гость их получил.
        """
        raise AcquiringError(
            f"{self.title}: возврат через приложение не поддерживается — "
            "верните платёж в личном кабинете банка."
        )

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

    def refund(self, payment) -> str:
        raise AcquiringError("Онлайн-оплата у заведения не подключена")


#: Корневой сертификат Минцифры. Т-Банк перевёл securepay на сертификаты
#: Russian Trusted CA, а их нет ни в certifi, ни в образе python:slim —
#: без этого файла любой запрос к банку падал бы на проверке TLS, и гость
#: видел бы «Т-Касса недоступна». Доверяем ему только в запросах к
#: Т-Банку, а не всему процессу.
RUSSIAN_TRUSTED_ROOT = Path(__file__).resolve().parent / "certs" / "russian_trusted_root_ca.pem"


@lru_cache(maxsize=1)
def _russian_tls() -> ssl.SSLContext:
    """Обычные корни (certifi) плюс корень Минцифры."""
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cafile=str(RUSSIAN_TRUSTED_ROOT))
    return context


class TBankAcquirer(BaseAcquirer):
    """Т-Касса (Т-Банк), developer.tbank.ru/eacq.

    Подпись у Т-Кассы одна и та же в обе стороны: значения полей верхнего
    уровня сортируются по имени ключа, к ним добавляется пароль терминала,
    всё склеивается и берётся SHA-256. Поэтому одна функция _token()
    и подписывает наш запрос, и проверяет их уведомление.

    Тестовый терминал (ключ оканчивается на DEMO) живёт на том же адресе,
    что и боевой, — отдельного переключателя не нужно.
    """

    name = "tbank"
    title = "Т-Банк (Т-Касса)"
    api = "https://securepay.tinkoff.ru/v2"
    #: Т-Банк считает уведомление доставленным, только если в ответ пришло
    #: ровно «OK». На что-то другое повторяет его раз в час сутки, а потом
    #: раз в сутки месяц.
    ack = "OK"
    #: Адрес уведомлений уходит банку с каждым платежом (NotificationURL),
    #: прописывать его в кабинете не нужно.
    sends_callback_url = True

    fields = (
        Field("terminal_key", "Ключ терминала", env="TBANK_TERMINAL_KEY",
              hint="Кабинет Т-Бизнеса → Интернет-эквайринг → Магазины → Терминалы. "
                   "Для проверки — тестовый терминал, его ключ оканчивается на DEMO",
              secret=False),
        Field("password", "Пароль терминала", env="TBANK_PASSWORD"),
    )

    #: Конечные статусы, при которых денег нет и не будет. Всё остальное —
    #: «ещё в работе»: незнакомый статус безопаснее переждать, чем отменить
    #: заказ, за который гость, возможно, уже заплатил.
    FAILED = frozenset({
        "REJECTED", "AUTH_FAIL", "CANCELED", "DEADLINE_EXPIRED", "ATTEMPTS_EXPIRED",
        "REVERSED", "PARTIAL_REVERSED", "REFUNDED", "PARTIAL_REFUNDED",
    })

    #: Ошибки, по которым видно, что не так с ключами, а не с запросом.
    BAD_KEYS = {
        "501": "банк не знает такого терминала — проверьте ключ терминала.",
        "204": "пароль не подходит к этому терминалу. Пароль берётся там же, "
               "где ключ, — у тестового и боевого терминала они разные.",
    }

    @property
    def terminal(self) -> str:
        return self.value("terminal_key")

    @property
    def password(self) -> str:
        return self.value("password")

    def _token(self, data: dict) -> str:
        # В подпись идут только простые поля: вложенные объекты (Receipt,
        # DATA) и сам Token исключаются, null банк тоже пропускает.
        parts = {
            k: v for k, v in data.items()
            if k != "Token" and v is not None and not isinstance(v, (dict, list))
        }
        parts["Password"] = self.password
        # Логическое значение банк пишет как в JSON — «true», а не «True».
        # В уведомлении всегда есть Success, и с питоньим str() подпись
        # не сошлась бы ни на одном настоящем уведомлении.
        joined = "".join(
            (("true" if v else "false") if isinstance(v, bool) else str(v))
            for v in (parts[k] for k in sorted(parts))
        )
        return hashlib.sha256(joined.encode()).hexdigest()

    @classmethod
    def _result(cls, external_id: str, status: str) -> Result:
        return Result(
            external_id=external_id,
            # AUTHORIZED — деньги захолдированы, но не списаны. Заказ
            # оплаченным считаем только после CONFIRMED.
            success=status == "CONFIRMED",
            pending=status != "CONFIRMED" and status not in cls.FAILED,
        )

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
            # Одностадийная оплата: заказ уже собран, держать холд и
            # подтверждать вторым запросом незачем. Без этого поля решает
            # настройка терминала, и при двухстадийной платёж навсегда
            # застрял бы в AUTHORIZED.
            "PayType": "O",
            "SuccessURL": return_url,
            "FailURL": return_url,
            # Адрес уведомлений — с каждым платежом, а не из кабинета: на
            # ЮKassa уведомление однажды не пришло вовсе, потому что адрес
            # в кабинет никто не вписал. Домен тот же, что у страницы
            # гостя, — по нему уведомление и находит заведение.
            "NotificationURL": _callback_url(return_url, self.name),
        }
        body["Token"] = self._token(body)

        data = self._post("Init", body)
        if not data.get("Success"):
            raise AcquiringError(self._reason(data, "Банк отклонил платёж"))

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
        return self._result(str(payload.get("PaymentId", "")), str(payload.get("Status", "")))

    def status(self, external_id: str) -> Result | None:
        """GetState. Ошибку сети наружу не глушим — см. BaseAcquirer.status."""
        self.require_configured()
        body = {"TerminalKey": self.terminal, "PaymentId": str(external_id)}
        body["Token"] = self._token(body)

        data = self._post("GetState", body)
        if not data.get("Success"):
            raise AcquiringError(self._reason(data, "Т-Касса не назвала статус платежа"))
        return self._result(str(external_id), str(data.get("Status", "")))

    def check(self) -> None:
        """Проверка ключей: CheckOrder по заказу, которого заведомо нет.

        Денег не двигает и платежей не создаёт. Неверный терминал и
        неверный пароль банк называет своими кодами — их и ловим. Любой
        другой ответ (в том числе «заказ не найден») значит, что подпись
        принята, то есть ключи верные.
        """
        self.require_configured()
        self.check_format()
        body = {"TerminalKey": self.terminal, "OrderId": "padacha-check"}
        body["Token"] = self._token(body)

        data = self._post("CheckOrder", body)
        code = str(data.get("ErrorCode", ""))
        if not data.get("Success") and code in self.BAD_KEYS:
            raise AcquiringError(f"{self.title}: {self.BAD_KEYS[code]}")

    def refund(self, payment) -> str:
        """Cancel у Т-Кассы делает и отмену холда, и возврат списанного —
        банк сам выбирает по состоянию платежа. Сумму не передаём: без неё
        возврат полный, а частичных мы и не делаем. Если к терминалу
        подключена онлайн-касса, чек возврата банк пробьёт сам."""
        self.require_configured()

        body = {"TerminalKey": self.terminal, "PaymentId": str(payment.external_id)}
        body["Token"] = self._token(body)

        data = self._post("Cancel", body)
        if not data.get("Success"):
            raise AcquiringError(self._reason(data, "Банк отказал в возврате"))
        return str(data.get("PaymentId") or payment.external_id)

    @staticmethod
    def _reason(data: dict, fallback: str) -> str:
        """Message у банка общий («Неверные параметры.»), суть — в Details."""
        message = str(data.get("Message") or "").strip()
        details = str(data.get("Details") or "").strip()
        if message and details:
            return f"{message} {details}"
        return message or details or fallback

    def _post(self, method: str, body: dict) -> dict:
        try:
            response = httpx.post(
                f"{self.api}/{method}", json=body, timeout=TIMEOUT, verify=_russian_tls()
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as e:
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

    def refund(self, payment) -> str:
        """У RBS-шлюза возврат — refund.do с суммой в копейках.

        Свой номер возврата шлюз не выдаёт: успех — это errorCode 0, и
        для сверки остаётся номер исходного заказа у банка.
        """
        self.require_configured()

        data = self._post(f"{self.api_url}/refund.do", {
            **self._auth(),
            "orderId": str(payment.external_id),
            "amount": _kopecks(payment.amount),
        })
        if data.get("errorCode") and str(data["errorCode"]) != "0":
            raise AcquiringError(data.get("errorMessage") or "Банк отказал в возврате")
        return str(payment.external_id)

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

    def refund(self, payment) -> str:
        """Возврат — отдельный объект у ЮKassa, со своим id и своим ключом
        идемпотентности. Ключ выводим из id платежа, как и при создании:
        повторное нажатие «вернуть» не отправит деньги дважды.
        """
        self.require_configured()

        data = self._post(f"{self.api}/refunds", {
            "payment_id": str(payment.external_id),
            "amount": {"value": _rubles(payment.amount), "currency": "RUB"},
        }, idempotence_key=str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"https://padacha.ru/refunds/{payment.pk}")
        ))

        refund_id = str(data.get("id") or "")
        if not refund_id:
            raise AcquiringError("ЮKassa не вернула номер возврата")
        # canceled — возврат отклонён; «succeeded» и «pending» оба значат,
        # что деньги пошли обратно (pending — банк гостя ещё проводит).
        if str(data.get("status")) == "canceled":
            raise AcquiringError("ЮKassa отклонила возврат")
        return refund_id

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
