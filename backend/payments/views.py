"""Ручки эквайринга: уведомление банка об оплате и настройка доступов.

Уведомление — отдельная публичная ручка, а не действие на заказе: оно
приходит от банка, без сессии сотрудника и без JWT. Подлинность
подтверждает сам провайдер — подписью (Т-Касса) или встречным запросом
статуса (Сбер, ЮKassa), см. payments/acquiring.py.

Настройка доступов — ручка владельца: какой банк и его ключи. Вместе
они здесь потому, что обе про эквайринг, а разделяет их только права.
"""
import logging

from django.http import HttpResponse
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import SiteSettings
from users.permissions import IsAdminRole, IsStaffRole

from .acquiring import AcquiringError, NoAcquirer, acquirer_class, acquirers, get_acquirer
from .models import AcquiringCredentials, Payment
from .services import apply_bank_result, note_payment_ok, payment_health

logger = logging.getLogger(__name__)


def _ack(acquirer, *, ok: bool = True):
    """Ответ банку. Т-Банку нужен ровно текст «OK» — на любой другой он
    повторяет уведомление месяц, — остальным хватает кода 200."""
    if acquirer.ack:
        return HttpResponse(acquirer.ack, content_type="text/plain")
    return Response({"ok": ok}, status=200)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def callback(request, provider: str):
    """POST /api/payments/callback/<provider>/ — уведомление банка.

    Отвечаем 200 почти всегда и намеренно: на любой другой код банк будет
    слать уведомление повторно, часами. Если оно чужое или подделано — мы
    просто ничего не делаем, но подтверждаем получение.
    """
    acquirer = get_acquirer(provider)
    payload = request.data if isinstance(request.data, dict) else {}

    result = acquirer.read_callback(payload, dict(request.headers))
    if result is None:
        logger.warning("Уведомление %s не прошло проверку", provider)
        return _ack(acquirer, ok=False)

    # Платёж ещё в работе: гость открыл страницу банка, но не заплатил.
    # Заказ трогать рано — иначе отменим его на полпути.
    if result.pending:
        return _ack(acquirer)

    payment = Payment.objects.filter(
        external_id=result.external_id, provider=acquirer.name
    ).first()
    if payment is None:
        logger.warning("Уведомление %s: платёж %s не найден", provider, result.external_id)
        return _ack(acquirer, ok=False)

    # Банки повторяют уведомления, и повтор по уже закрытому платежу
    # переписал бы closed_at и closed_by. Той же строкой отсекается и
    # гонка с опросом банка: кто пришёл вторым, тот ничего не делает
    # (проверка внутри — под блокировкой строки платежа).
    if not apply_bank_result(payment, result):
        return _ack(acquirer)

    logger.info(
        "Оплата %s: платёж %s → %s (заказ %s)",
        provider, result.external_id,
        "успех" if result.success else "отказ", payment.order_id,
    )
    return _ack(acquirer)


class AcquiringSettingsView(APIView):
    """GET/PUT/DELETE /api/acquiring/ — банк заведения и доступы к нему.

    Отдельная ручка, а не поля в /api/site/: тот публичный, и всему, что
    связано с ключами, там нечего делать. Здесь же только владелец —
    деньги идут на его расчётный счёт, и менять реквизиты приёма оплаты
    не должен ни склад, ни официант.

    Наружу не отдаём ни одного секрета, даже владельцу: по полю видно
    «задан» или «не задан». Показать введённый пароль терминала значило
    бы отдать его любому, кто на минуту сел за открытую панель.
    """

    permission_classes = [IsAdminRole]

    def get(self, request):
        return Response(self._state(request))

    def put(self, request):
        provider = str(request.data.get("provider", "")).strip()
        if provider not in [a.name for a in acquirers()] + [NoAcquirer.name]:
            return Response({"detail": "Неизвестный банк"}, status=400)

        values = request.data.get("values") or {}
        if not isinstance(values, dict):
            return Response({"detail": "Доступы должны быть объектом"}, status=400)

        site = SiteSettings.load()
        if provider != site.acquiring:
            site.acquiring = provider
            # Банк сменили — оплату гасим. Ключи нового ещё не проверены,
            # а гость упёрся бы в ошибку у самой кассы.
            site.online_payment_on = False
            site.save(update_fields=["acquiring", "online_payment_on"])

        if provider != NoAcquirer.name and values:
            try:
                self._save_credentials(provider, values)
            except AcquiringError as e:
                # Ключи не приняты банком — не сохраняем: иначе владелец
                # ушёл бы с ощущением «подключено», а ошибку первым увидел
                # бы гость, уже собравший заказ.
                return Response({"detail": str(e)}, status=400)

        return Response(self._state(request))

    def delete(self, request):
        """Стереть доступы выбранного банка (сменился договор, утёк ключ)."""
        site = SiteSettings.load()
        AcquiringCredentials.objects.filter(provider=site.acquiring).delete()
        if site.online_payment_on:
            site.online_payment_on = False
            site.save(update_fields=["online_payment_on"])
        return Response(self._state(request))

    def _save_credentials(self, provider: str, values: dict) -> None:
        known = {f.key for f in acquirer_class(provider).fields}
        row = AcquiringCredentials.objects.filter(provider=provider).first()
        stored = row.values() if row else {}
        for key, value in values.items():
            if key not in known or not isinstance(value, str):
                continue
            # Пустое поле — «не трогай»: форма присылает пустым то, что
            # владелец не менял (значения-то ей не показывают). Стереть
            # всё разом — отдельная кнопка, DELETE.
            if value.strip():
                stored[key] = value.strip()
        # Проверяем то, что получилось, ДО записи: банк отвечает на
        # пробный запрос за доли секунды, а неверный ключ иначе всплывёт
        # только на первом живом платеже.
        acquirer_class(provider)(stored).check()
        if row is None:
            row = AcquiringCredentials(provider=provider)
        row.set_values(stored)
        row.save()
        # Банк ключи принял — прежняя ошибка, скорее всего, была в них.
        # Не убрать её значило бы пугать владельца уже починенным.
        note_payment_ok(provider)

    def _state(self, request) -> dict:
        site = SiteSettings.load()
        acquirer = get_acquirer(site.acquiring)
        return {
            "provider": site.acquiring,
            "ready": acquirer.configured(),
            "online_payment_on": site.online_payment_on,
            "filled": acquirer.filled(),
            # Адрес уведомлений владелец вписывает в кабинете банка сам —
            # взять его больше неоткуда, а без него банк молчит об оплате.
            # Домен берём из запроса: у каждого заведения он свой, по нему
            # уведомление и находит нужное кафе.
            "callback_url": (
                request.build_absolute_uri(f"/api/payments/callback/{acquirer.name}/")
                if site.acquiring != NoAcquirer.name
                else ""
            ),
            # Банк сам получает адрес с каждым платежом — в кабинет его
            # вписывать не нужно, показываем лишь для сверки.
            "callback_auto": acquirer.sends_callback_url,
            # Последний отказ банка, после которого гости так и не смогли
            # перейти к оплате. Гость причину не видит — только владелец.
            "last_error": (
                payment_health(site.acquiring)
                if site.acquiring != NoAcquirer.name
                else None
            ),
            "banks": [
                {
                    "name": cls.name,
                    "title": cls.title,
                    "fields": [
                        {
                            "key": f.key,
                            "label": f.label,
                            "hint": f.hint,
                            "secret": f.secret,
                            "required": f.required,
                        }
                        for f in cls.fields
                    ],
                }
                for cls in acquirers()
            ],
        }


class KassaSettingsView(APIView):
    """GET/PUT/DELETE /api/kassa/ — касса заведения и ключ к ней.

    Устроена как ручка банков: только владелец, секреты наружу не отдаются
    — видно лишь «задан». Необязательные несекретные поля (магазин, ставка
    НДС) отдаём значениями: без них форма не покажет, что выбрано сейчас.
    """

    permission_classes = [IsAdminRole]

    def get(self, request):
        return Response(self._state())

    def put(self, request):
        from .providers import NONE, KassaError, kassas, provider_class

        provider = str(request.data.get("provider", "")).strip()
        if provider not in [k.name for k in kassas()] + [NONE]:
            return Response({"detail": "Неизвестная касса"}, status=400)
        values = request.data.get("values") or {}
        if not isinstance(values, dict):
            return Response({"detail": "Доступы должны быть объектом"}, status=400)

        if provider != NONE:
            cls = provider_class(provider)
            known = {f.key for f in cls.fields}
            row = AcquiringCredentials.objects.filter(provider=provider).first()
            stored = row.values() if row else {}
            for key, value in values.items():
                if key in known and isinstance(value, str) and value.strip():
                    stored[key] = value.strip()
            # Проверяем у кассы до записи и до переключения: иначе неверный
            # ключ первым найдёт гость, нажавший «Оплатить на кассе».
            try:
                cls(stored).check()
            except KassaError as e:
                return Response({"detail": str(e)}, status=400)
            if row is None:
                row = AcquiringCredentials(provider=provider)
            row.set_values(stored)
            row.save()
            note_payment_ok(provider)

        site = SiteSettings.load()
        if provider != site.kassa:
            site.kassa = provider
            site.save(update_fields=["kassa"])
        return Response(self._state())

    def delete(self, request):
        """Стереть ключ кассы и отключить её (сменили кассу, утёк ключ)."""
        from .providers import NONE

        site = SiteSettings.load()
        if site.kassa != NONE:
            AcquiringCredentials.objects.filter(provider=site.kassa).delete()
            site.kassa = NONE
            site.save(update_fields=["kassa"])
        return Response(self._state())

    def _state(self) -> dict:
        from .kassa_sync import sync_state
        from .providers import NONE, get_provider, kassa_available, kassas

        site = SiteSettings.load()
        chosen = site.kassa != NONE
        provider = get_provider(site.kassa) if chosen else None
        return {
            "provider": site.kassa,
            "ready": kassa_available(),
            # Синхронизация терминала через кабинет: null — не настроена.
            "sync": sync_state() if chosen else None,
            "filled": provider.filled() if provider else {},
            # Последний отказ кассы, после которого заказы на неё так и не
            # уходили. Гость на «Оплатить на кассе» причину не видит.
            "last_error": payment_health(site.kassa) if chosen else None,
            "values": {
                f.key: provider.value(f.key)
                for f in (provider.fields if provider else ())
                if not f.secret
            },
            "kassas": [
                {
                    "name": cls.name,
                    "title": cls.title,
                    "fields": [
                        {
                            "key": f.key,
                            "label": f.label,
                            "hint": f.hint,
                            "secret": f.secret,
                            "required": f.required,
                            "choices": [{"value": v, "label": t} for v, t in f.choices],
                            "default": f.default,
                        }
                        for f in cls.fields
                    ],
                }
                for cls in kassas()
            ],
        }


class KassaResyncView(APIView):
    """POST /api/kassa/resync/ — синхронизировать терминал кассы с облаком.

    Жмёт бариста, когда отправленного заказа нет на кассе (сами по себе не
    синхронизируем, см. kassa_sync), и владелец в панели. Доступна любому
    сотруднику: у окна стоит бариста, а не владелец.
    """

    permission_classes = [IsStaffRole]

    def post(self, request):
        from .kassa_sync import resync_kassa
        from .providers import KassaError

        try:
            done = resync_kassa()
        except KassaError as e:
            return Response({"detail": str(e)}, status=400)
        return Response({
            "done": done,
            "detail": "Касса синхронизирована — заказы сейчас появятся на ней"
            if done else "Синхронизация уже идёт — подождите полминуты",
        })


class PaymentJournalView(APIView):
    """GET /api/payments/journal/?date=ГГГГ-ММ-ДД — платежи за день.

    Только владелец: это выручка заведения. Без даты — сегодня.
    """

    permission_classes = [IsAdminRole]

    def get(self, request):
        from datetime import date

        from django.utils import timezone

        from .journal import day_journal

        raw = request.query_params.get("date")
        try:
            day = date.fromisoformat(raw) if raw else timezone.localdate()
        except ValueError:
            return Response({"detail": "Дата в формате ГГГГ-ММ-ДД"}, status=400)
        return Response(day_journal(day))
