"""Ручки эквайринга: уведомление банка об оплате и настройка доступов.

Уведомление — отдельная публичная ручка, а не действие на заказе: оно
приходит от банка, без сессии сотрудника и без JWT. Подлинность
подтверждает сам провайдер — подписью (Т-Касса) или встречным запросом
статуса (Сбер, ЮKassa), см. payments/acquiring.py.

Настройка доступов — ручка владельца: какой банк и его ключи. Вместе
они здесь потому, что обе про эквайринг, а разделяет их только права.
"""
import logging

from django.db import transaction
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import SiteSettings
from users.permissions import IsAdminRole

from .acquiring import AcquiringError, NoAcquirer, acquirer_class, acquirers, get_acquirer
from .models import AcquiringCredentials, Payment
from .services import apply_payment_result

logger = logging.getLogger(__name__)


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
        return Response({"ok": False}, status=200)

    # Платёж ещё в работе: гость открыл страницу банка, но не заплатил.
    # Заказ трогать рано — иначе отменим его на полпути.
    if result.pending:
        return Response({"ok": True})

    payment = Payment.objects.filter(
        external_id=result.external_id, provider=acquirer.name
    ).first()
    if payment is None:
        logger.warning("Уведомление %s: платёж %s не найден", provider, result.external_id)
        return Response({"ok": False}, status=200)

    # Банки повторяют уведомления, и повтор по уже оплаченному заказу
    # переписал бы closed_at и closed_by. Отвечаем «принято» и выходим.
    if payment.status == Payment.Status.SUCCEEDED:
        return Response({"ok": True})

    with transaction.atomic():
        apply_payment_result(
            payment, success=result.success, fiscal_receipt=result.fiscal_receipt
        )
    logger.info(
        "Оплата %s: платёж %s → %s",
        provider, result.external_id, "успех" if result.success else "отказ",
    )
    return Response({"ok": True})


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
