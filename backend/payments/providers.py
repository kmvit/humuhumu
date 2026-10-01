"""Оплата через кассу заведения: заказ уходит на кассу, гость платит там.

Не путать с payments/acquiring.py — там гость платит картой онлайн на
странице банка. Здесь деньги принимает бариста на своей кассе: наличными
или картой через её терминал, и чек печатает сама касса. Наша задача —
положить на кассу заказ с позициями (чтобы их не вбивали руками) и узнать,
что его оплатили, чтобы пустить заказ в работу.

Касса выбирается настройкой заведения (SiteSettings.kassa), доступы лежат
там же, где ключи банков, — в AcquiringCredentials, зашифрованные и без
сериализатора (см. докстроку модели). Имена касс с именами банков не
пересекаются, поэтому одной таблицы хватает обоим.

Результат, чем бы он ни пришёл, применяется services.apply_payment_result,
как и у онлайн-оплаты.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

import httpx
from django.conf import settings

from .acquiring import Field

logger = logging.getLogger(__name__)

#: Сколько ждём кассу. Бариста стоит с гостем у окна — долго висеть нельзя.
TIMEOUT = 15.0


class KassaError(Exception):
    """Касса отказала или не ответила. Текст видит сотрудник, не гость."""


class KassaNotFound(KassaError):
    """Касса не знает такого заказа — его удалили на ней самой."""


@dataclass(frozen=True)
class KassaResult:
    """Что касса сказала о заказе."""

    #: Заказ оплачен на кассе.
    success: bool
    #: Ещё не оплачен и не отменён — гость идёт к кассе.
    pending: bool = False
    #: Чем заплатили: cash / card (Payment.Method). Пусто — касса не сказала.
    method: str = ""
    fiscal_receipt: str = ""
    #: Для возврата через кассу: чек, слип карточной оплаты, RRN у банка.
    receipt_id: str = ""
    slip_id: str = ""
    rrn: str = ""


@dataclass(frozen=True)
class KassaOperation:
    """Состояние операции, которую касса выполняет по нашей команде."""

    #: Касса ещё работает: в очереди, ждёт карту гостя, печатает.
    running: bool
    #: Выполнена. Иначе (и не running) — отказ, отмена или таймаут.
    done: bool = False
    #: Что вернула касса: слип возврата или пробитый чек.
    result: dict | None = None
    #: Почему не вышло — для сотрудника.
    message: str = ""


class BaseProvider:
    """Контракт кассы."""

    #: Умеет ли касса вернуть деньги по нашей команде (иначе — руками на ней).
    can_refund = False

    name = "base"
    title = "—"
    #: Какие доступы нужны этой кассе (см. acquiring.Field).
    fields: tuple[Field, ...] = ()

    def __init__(self, credentials: dict | None = None) -> None:
        self._credentials = credentials or {}

    def value(self, key: str) -> str:
        """Значение доступа, а для необязательного поля — его умолчание."""
        stored = str(self._credentials.get(key, "")).strip()
        if stored:
            return stored
        field = next((f for f in self.fields if f.key == key), None)
        return field.default if field else ""

    def configured(self) -> bool:
        """Заданы ли доступы. Без них кнопку «Оплатить на кассе» не показываем."""
        return bool(self.fields) and all(
            str(self._credentials.get(f.key, "")).strip() for f in self.fields if f.required
        )

    def filled(self) -> dict[str, bool]:
        """Какие поля заданы — для панели владельца (без самих значений)."""
        return {f.key: bool(str(self._credentials.get(f.key, "")).strip()) for f in self.fields}

    def require_configured(self) -> None:
        if not self.configured():
            raise KassaError(
                f"Не заданы доступы к кассе ({self.title}). Их вводит владелец "
                "в панели, раздел «Оплата»."
            )

    def check(self) -> None:
        """Проверить доступы у кассы. Молча — значит приняты.

        Как и у банков: неверный ключ должен найти владелец, пока вводит
        его, а не гость, который уже стоит у кассы.
        """
        self.require_configured()
        for f in self.fields:
            value = str(self._credentials.get(f.key, "")).strip()
            if value and f.pattern and not re.fullmatch(f.pattern, value):
                raise KassaError(
                    f"{self.title}: поле «{f.label}» заполнено не тем значением."
                    + (f" {f.error}" if f.error else "")
                )

    def start(self, payment) -> None:
        """Положить заказ на кассу. Обязан проставить payment.external_id."""
        raise NotImplementedError

    def status(self, payment) -> KassaResult | None:
        """Что с заказом на кассе. None — спросить не умеем."""
        return None

    def cancel(self, payment) -> None:
        """Снять заказ с кассы: его оплатили иначе или гость передумал.

        Иначе он так и висит в «отложенных», и кассир может взять за него
        деньги второй раз.
        """
        return None


class MockProvider(BaseProvider):
    """Заглушка кассы: «отправляет» заказ, результат подаёт дев-ручка pay_result."""

    name = "mock"
    title = "Эмуляция (для разработки)"

    def configured(self) -> bool:
        return True

    def start(self, payment) -> None:
        payment.external_id = f"mock-{payment.pk}"
        payment.save(update_fields=["external_id"])


#: Ставки НДС aQsi (тег 1199). Кафе на УСН до порога работает без НДС,
#: с 2026 года часть из них платит 5 или 7 %, на ОСН — 22 %.
AQSI_VAT = (
    ("6", "Без НДС"),
    ("7", "НДС 5 %"),
    ("8", "НДС 7 %"),
    ("2", "НДС 10 %"),
    ("1", "НДС 22 %"),
)

#: Типы оплаты в чеке aQsi: 0 — наличные, 1 — безналичные.
AQSI_CASH, AQSI_CARD = 0, 1


class AqsiProvider(BaseProvider):
    """Касса aQsi — на ней же работают смарт-кассы Т-Банка.

    Заказ ложится на кассу «отложенным заказом»: бариста открывает его в
    разделе «Отложенные заказы», принимает наличные или карту, касса
    печатает чек. О результате узнаём опросом: веб-хуки aQsi сообщают
    только об «операциях устройства» (удалённая оплата, чек по API), а
    оплату отложенного заказа они не покрывают.

    Номер заказа на кассе — наш id платежа, выведенный в UUID. Метод
    создания у aQsi — «создать или обновить», поэтому повтор после обрыва
    связи не положит на кассу второй заказ.
    """

    name = "aqsi"
    title = "aQsi (смарт-касса Т-Банка)"
    #: Боевой сервер. У aQsi есть ещё VIP-сервер (api-vip.aqsi.ru) — на него
    #: установку переключает переменная AQSI_API_URL, код не трогаем.
    default_api = "https://api.aqsi.ru/pub"
    fields = (
        Field("api_key", "API-ключ", env="",
              hint="Личный кабинет aQsi → Настройки → Интеграции → "
                   "Интеграция по внешнему API → Добавить",
              pattern=r"\S{16,}",
              error="Ключ длинный — скопируйте его из кабинета aQsi целиком."),
        Field("shop_id", "Магазин", env="",
              hint="Если магазин в кабинете aQsi один — оставьте пустым",
              secret=False, required=False),
        Field("vat", "Ставка НДС", env="", secret=False, required=False,
              choices=AQSI_VAT, default="6"),
    )

    @property
    def api(self) -> str:
        return (os.getenv("AQSI_API_URL") or self.default_api).rstrip("/")

    @property
    def headers(self) -> dict:
        return {"x-client-key": f"Application {self.value('api_key')}"}

    def order_id(self, payment) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://padacha.ru/kassa/{payment.pk}"))

    def check(self) -> None:
        super().check()
        # Ключ проверяем самым дешёвым запросом, а заодно — что магазин
        # находится: без него касса заказ не примет.
        self.shop()

    def shop(self) -> str:
        """Магазин, на кассы которого кладём заказ."""
        shops = self._request("GET", "/v2/Shops/list")
        if not isinstance(shops, list) or not shops:
            raise KassaError("aQsi: в кабинете нет ни одного магазина")
        wanted = self.value("shop_id")
        if wanted:
            for s in shops:
                if wanted in (str(s.get("id")), str(s.get("name"))):
                    return str(s["id"])
            raise KassaError(f"aQsi: магазин «{wanted}» не найден в кабинете")
        if len(shops) > 1:
            names = ", ".join(str(s.get("name") or s.get("id")) for s in shops)
            raise KassaError(
                f"aQsi: в кабинете несколько магазинов ({names}) — укажите нужный в поле «Магазин»"
            )
        return str(shops[0]["id"])

    def start(self, payment) -> None:
        self.require_configured()
        order = payment.order
        body = {
            "id": self.order_id(payment),
            # Номер, по которому бариста найдёт заказ на кассе. Он же
            # показан гостю и на карточке у баристы.
            "number": str(order.pk),
            "dateTime": order.created_at.isoformat(),
            "shop": self.shop(),
            "comment": (order.customer_name or "Заказ с сайта")[:200],
            # Сумму на кассе не правят: иначе она разойдётся с заказом, и
            # мы не поймём, за что именно заплатили.
            "isEditableByDevice": False,
            "content": {
                "type": 1,  # приход
                "positions": self._positions(order),
                # Бонусы гостя — скидкой на чек: к оплате ровно order.payable.
                "discountMoney": float(order.bonus_spent or 0),
            },
        }
        self._request("POST", "/v2/Orders/simple", json=body)
        payment.external_id = body["id"]
        payment.save(update_fields=["external_id", "updated_at"])

    def _positions(self, order) -> list[dict]:
        vat = int(self.value("vat") or 6)
        positions = []
        for item in order.items.all():
            name = item.display_name
            if item.options_text:
                name = f"{name} ({item.options_text})"
            positions.append({
                "text": name[:128],
                "quantity": item.quantity,
                # Цена за порцию с опциями: так чек сходится с заказом.
                "price": float((item.unit_price or 0) + item.modifiers_total),
                "tax": vat,
                "paymentMethodType": 4,   # полный расчёт
                "paymentSubjectType": 1,  # товар
                "unitCode": 0,            # штука
                "editable": False,
            })
        return positions

    def status(self, payment) -> KassaResult | None:
        try:
            data = self._request("GET", f"/v2/Orders/simple/{payment.external_id}")
        except KassaNotFound:
            # Кассир удалил заказ у себя. Держать платёж «ждущим» нельзя:
            # заказ висел бы «на кассе» вечно и не отменился бы сам.
            return KassaResult(success=False)
        if not isinstance(data, dict):
            return None
        state = str(data.get("status") or "").strip().lower()
        if state == "оплачен":
            info = self._from_receipts(data.get("receipts") or [])
            return KassaResult(success=True, **info)
        if state in ("отменен", "отменён"):
            return KassaResult(success=False)
        # «Отложен», «Черновик», «Част. оплачен» — ещё не всё.
        return KassaResult(success=False, pending=True)

    @staticmethod
    def _from_receipts(receipts: list) -> dict:
        """Что взять из чека, который пробила касса.

        Чем заплатили и номер чека — для нас и владельца. Id чека и слипа
        с RRN — для возврата через кассу: касса возвращает деньги на карту
        по id исходного слипа, а он лежит только здесь, в acquiringData
        платежа по карте. У наличных слипа нет.
        """
        method, fiscal, receipt_id, slip_id, rrn = "", "", "", "", ""
        for receipt in receipts:
            if not isinstance(receipt, dict):
                continue
            content = receipt.get("content") or {}
            if content.get("type") not in (None, 1):
                continue  # возврат — не наш случай
            pays = [
                p for p in ((content.get("checkClose") or {}).get("payments") or [])
                if isinstance(p, dict)
            ]
            types = {p.get("type") for p in pays}
            if types:
                method = "cash" if types == {AQSI_CASH} else "card"
            for p in pays:
                slip = p.get("acquiringData") if p.get("type") == AQSI_CARD else None
                if isinstance(slip, dict) and slip.get("id"):
                    slip_id = str(slip["id"])[:64]
                    rrn = str(slip.get("rrn") or "")[:32]
            receipt_id = str(receipt.get("id") or "")[:64]
            number, fp = receipt.get("documentNumber"), receipt.get("fp")
            if number or fp:
                fiscal = f"ФД {number or '—'}, ФП {fp or '—'}"[:64]
        return {
            "method": method, "fiscal_receipt": fiscal,
            "receipt_id": receipt_id, "slip_id": slip_id, "rrn": rrn,
        }

    # ── Возврат через кассу ────────────────────────────────────────────
    # Две операции устройства подряд: вернуть деньги на карту по слипу
    # оплаты и пробить чек возврата. Обе асинхронные — касса может попросить
    # гостя приложить карту, — поэтому здесь только запуск и опрос, а
    # доводит возврат payments.services.

    can_refund = True

    #: Статусы операции устройства, когда касса ещё не закончила.
    RUNNING = ("Pending", "Processing", "Finishing")

    def receipt(self, receipt_id: str) -> dict:
        """Исходный чек продажи: позиции, оплаты, касса, налогообложение."""
        data = self._request("GET", f"/v4/Receipts/{receipt_id}")
        if not isinstance(data, dict) or not data.get("positions"):
            raise KassaError("aQsi не вернула исходный чек — вернуть через кассу не выйдет")
        return data

    @staticmethod
    def receipt_device(receipt: dict) -> int:
        device = (receipt.get("device") or {}).get("id")
        if not device:
            raise KassaError("В исходном чеке нет кассы, на которой он пробит")
        return int(device)

    def start_slip_refund(self, receipt: dict, slip_id: str) -> str:
        """Вернуть деньги на карту — полностью, по слипу исходной оплаты."""
        data = self._request("POST", "/v4/Slips/process/refund", json={
            "deviceId": self.receipt_device(receipt),
            "originalId": slip_id,
        })
        return self._operation_id(data)

    def start_return_receipt(self, receipt: dict, refund_slip: dict | None) -> str:
        """Пробить чек возврата прихода — зеркало исходного чека.

        Позиции, налогообложение и касса — из чека продажи: возврат должен
        совпасть с ним до копейки. Карточная часть идёт со слипом возврата,
        наличная — без: деньги бариста отдаёт из ящика.
        """
        info = receipt.get("info") or {}
        positions = []
        for pos in receipt.get("positions") or []:
            pinfo = {k: v for k, v in (pos.get("info") or {}).items() if k in self.POSITION_KEYS}
            item = {"info": pinfo}
            if pos.get("id"):
                item["id"] = pos["id"]
            positions.append(item)
        payments = []
        for pay in receipt.get("payments") or []:
            entry = {"type": pay.get("type"), "amount": pay.get("amount")}
            if pay.get("type") == AQSI_CARD:
                if not refund_slip:
                    raise KassaError("Нет слипа возврата для карточной части чека")
                entry["slip"] = refund_slip
            payments.append(entry)
        body = {
            "deviceId": self.receipt_device(receipt),
            "typeId": 2,  # возврат прихода
            "info": {"taxSystemCode": info.get("taxSystemCode")},
            "positions": positions,
            "payments": payments,
            "ignoreItemCodeCheck": True,
            "skipPrinting": False,
        }
        # Скидка чека (бонусы гостя): если позиции в сумме дороже оплаты,
        # разница — скидка, и чек возврата должен её повторить.
        total = sum(
            int(p["info"].get("finalPrice") or 0) * float(p["info"].get("baseQuantity") or 1)
            for p in positions
        )
        paid = sum(int(p.get("amount") or 0) for p in payments)
        if round(total) > paid:
            body["discountInfo"] = {"type": "Absolute", "value": int(round(total)) - paid}
        data = self._request("POST", "/v4/Receipts/process", json=body)
        return self._operation_id(data)

    #: Поля позиции, которые принимает чек по API (ReceiptPositionInfoReqV3).
    POSITION_KEYS = (
        "calculationTypeId", "calculationSubjectId", "name", "taxRateId", "quantityUnitText",
        "quantityUnitId", "baseQuantity", "quantityPerBatch", "finalPrice", "agentInfo",
        "supplierInfo", "nomenclatureCode", "excise", "countryCode", "declarationNumber",
        "additionalAttribute", "industryAttributes",
    )

    def operation(self, operation_id: str) -> KassaOperation:
        data = self._request("GET", f"/v4/Operations/{operation_id}")
        state = str((data or {}).get("status") or "")
        if state in self.RUNNING:
            return KassaOperation(running=True)
        result = None
        raw = (data or {}).get("result")
        if isinstance(raw, str) and raw:
            try:
                result = json.loads(raw)
            except ValueError:
                result = None
        elif isinstance(raw, dict):
            result = raw
        if state == "Completed":
            return KassaOperation(running=False, done=True, result=result)
        reason = {
            "Canceled": "операцию отменили на кассе",
            "Timeout": "касса не успела — истекло время",
        }.get(state, "")
        message = (data or {}).get("message") or (data or {}).get("problems") or reason or state
        return KassaOperation(running=False, done=False, result=result, message=str(message)[:300])

    @staticmethod
    def _operation_id(data) -> str:
        op = (data or {}).get("operationId") if isinstance(data, dict) else None
        if not op:
            raise KassaError("aQsi не вернула номер операции")
        return str(op)

    def cancel(self, payment) -> None:
        if not payment.external_id:
            return
        try:
            self._request("DELETE", f"/v2/Orders/simple/{payment.external_id}")
        except KassaNotFound:
            pass  # на кассе его уже нет — того и добивались

    def _request(self, method: str, path: str, *, json: dict | None = None):
        try:
            response = httpx.request(
                method, f"{self.api}{path}", json=json, headers=self.headers, timeout=TIMEOUT
            )
        except httpx.HTTPError as e:
            raise KassaError(f"aQsi недоступна: {e}") from e
        if response.status_code in (401, 403):
            raise KassaError("aQsi не приняла API-ключ — проверьте его в кабинете aQsi")
        if response.status_code >= 400:
            reason = self._reason(response)
            # Несуществующий заказ aQsi отдаёт 412 «Заказ не найден»
            # (проверено на боевом кабинете), а не 404.
            if response.status_code in (404, 412) and "не найден" in reason.lower():
                raise KassaNotFound(f"aQsi: {reason}")
            raise KassaError(f"aQsi: {reason}")
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        """Причину отказа aQsi пишет в тело, и по-разному: {"errors": [...]},
        {"message": ..., "errors": [...]} или {"message": ..., "reason": ...}."""
        try:
            data = response.json()
        except ValueError:
            return f"ответ {response.status_code}"
        errors = data.get("errors") if isinstance(data, dict) else None
        if isinstance(errors, list) and errors:
            parts = [e if isinstance(e, str) else (e.get("message") or e.get("text") or str(e))
                     for e in errors]
            return "; ".join(str(p) for p in parts)[:300]
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])[:300]
        return str((data or {}).get("code") or f"ответ {response.status_code}")


# Реестр касс. Новая касса добавится сюда ещё одним классом.
_PROVIDERS = {p.name: p for p in (MockProvider, AqsiProvider)}

#: Нет кассы: заведение отмечает оплату у себя руками.
NONE = "none"


def provider_class(name: str) -> type[BaseProvider] | None:
    return _PROVIDERS.get(name)


def kassas() -> list[type[BaseProvider]]:
    """Кассы для формы владельца — без эмуляции."""
    return [p for p in _PROVIDERS.values() if p is not MockProvider]


def is_kassa(name: str) -> bool:
    """Платёж провёл кассовый провайдер (а не банк и не ручная отметка)."""
    return name in _PROVIDERS


def stored_credentials(name: str) -> dict:
    """Доступы ТЕКУЩЕГО заведения к кассе. Фильтр по заведению — в менеджере."""
    from .acquiring import stored_credentials as stored

    return stored(name)


def get_provider(name: str | None = None) -> BaseProvider:
    """Касса заведения с её доступами. Без аргумента — из настроек.

    Кассы нет — эмуляция: так работал каркас до подключения настоящей
    кассы, и дев-кнопки официанта на неё рассчитаны.
    """
    if name is None:
        from core.models import SiteSettings

        name = SiteSettings.load().kassa
        if name == NONE:
            name = getattr(settings, "PAYMENT_PROVIDER", MockProvider.name)
    cls = provider_class(name) or MockProvider
    if cls is MockProvider:
        return MockProvider()
    return cls(stored_credentials(cls.name))


def kassa_only() -> bool:
    """Стойка с подключённой кассой: деньги принимаются только через кассу.

    Решение владельца Монти: мимо кассы ничего не принимаем, касса легла —
    ждём, пока заработает. Ручных отметок «наличными / картой» здесь нет
    ни у гостевого заказа, ни у заказа, который завёл бариста. Зал не
    трогаем: там счёт закрывает официант, и касса к нему пока не подключена.
    """
    from core.models import SiteSettings

    if SiteSettings.load().service_mode != SiteSettings.ServiceMode.COUNTER:
        return False
    return kassa_available()


def kassa_available() -> bool:
    """Подключена ли у заведения настоящая касса с доступами."""
    from core.models import SiteSettings

    name = SiteSettings.load().kassa
    cls = provider_class(name)
    if cls is None or cls is MockProvider:
        return False
    return cls(stored_credentials(cls.name)).configured()
