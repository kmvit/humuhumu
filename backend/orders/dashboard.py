"""«Сводка» владельца: деньги за период, сравнение и что требует внимания.

Раньше админка тянула в браузер все заказы за всё время и складывала их
там: цифры не были привязаны к периоду («578 заказов» рядом с «оборот 0»),
а страница с каждым месяцем грузилась бы всё дольше. Теперь считаем здесь
и отдаём готовое.

Деньги считаем по факту оплаты (paid_at), как и прежняя сводка: на стойке
с предоплатой оплаченный заказ ещё «в работе», но деньги по нему уже в
кассе. Выручка — полученные деньги: чек минус бонусы, минус возвраты,
сделанные в этот же период (как в «Финансах»: возврат вычитается днём
возврата, а не днём продажи). Штрафной стол не в счёт — это подарки.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import F, OuterRef, Subquery, Sum
from django.utils import timezone

from payments.journal import ONLINE, channel_of
from payments.models import Payment

from .models import Order, OrderItem

PERIODS = ("today", "week", "month")

# Сколько ждать, прежде чем звать владельца: гость у стола или платёж на
# терминале, висящие дольше, — уже не «сейчас обработают», а забыли.
STUCK_MINUTES = 10
TOP_LIMIT = 5


def _money(v) -> Decimal:
    return (v or Decimal("0")).quantize(Decimal("0.01"))


def _start_of(day) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def windows(period: str, now: datetime) -> tuple[datetime, datetime, datetime, datetime]:
    """Текущее окно и окно для сравнения: (cur_from, cur_to, prev_from, prev_to).

    Сравниваем одинаковые по длине отрезки, обрезанные «на это же время»:
    сегодня до 15:00 — с тем же днём недели неделю назад до 15:00, неделю
    с понедельника — с прошлой неделей до того же часа. Иначе полдня
    всегда проигрывали бы целому вчерашнему дню. Сегодня меряем прошлой
    неделей, а не вчера: у кафе вторник со вторником, а не с воскресеньем.
    """
    today = timezone.localdate(now)
    if period == "today":
        start = _start_of(today)
        prev_start = start - timedelta(days=7)
    elif period == "week":
        start = _start_of(today - timedelta(days=today.weekday()))
        prev_start = start - timedelta(days=7)
    else:
        first = today.replace(day=1)
        start = _start_of(first)
        prev_first = (first - timedelta(days=1)).replace(day=1)
        prev_start = _start_of(prev_first)
    elapsed = now - start
    prev_end = prev_start + elapsed
    # в коротком прошлом месяце (31-е против февраля) не залезаем в текущий
    return start, now, prev_start, min(prev_end, start)


def _base(penalty_table: str):
    qs = Order.objects.all()
    if penalty_table:
        qs = qs.exclude(table=penalty_table)
    return qs


def _sold(penalty_table: str, start, end):
    """Оплаченные в окне заказы. Возвращённые остаются: продажа была."""
    return _base(penalty_table).filter(
        paid_at__gte=start, paid_at__lt=end
    ).exclude(status=Order.Status.CANCELLED)


def _refunded(penalty_table: str, start, end):
    return _base(penalty_table).filter(
        status=Order.Status.REFUNDED, refunded_at__gte=start, refunded_at__lt=end
    )


def totals(penalty_table: str, start, end) -> dict:
    """Деньги окна: выручка, чеки, средний чек, разбивка по каналам, возвраты."""
    received = F("total") - F("bonus_spent")
    rows = list(
        _sold(penalty_table, start, end)
        .annotate(
            provider=Subquery(
                Payment.objects.filter(
                    order=OuterRef("pk"),
                    purpose=Payment.Purpose.ORDER,
                    status__in=(Payment.Status.SUCCEEDED, Payment.Status.REFUNDED),
                )
                .order_by("-created_at")
                .values("provider")[:1]
            ),
            received=received,
        )
        .values("received", "bonus_spent", "pay_method", "provider")
    )
    cash = card = online = bonuses = Decimal("0")
    for r in rows:
        amount = r["received"] or Decimal("0")
        bonuses += r["bonus_spent"] or Decimal("0")
        # карта онлайн и карта на кассе — разные деньги на разных счетах
        if channel_of(r["provider"] or "") == ONLINE:
            online += amount
        elif r["pay_method"] == Order.PayMethod.CASH:
            cash += amount
        else:
            card += amount
    sales = cash + card + online
    refunds = _refunded(penalty_table, start, end).aggregate(s=Sum(received))["s"]
    refunds = _money(refunds)
    checks = len(rows)
    return {
        "revenue": _money(sales - refunds),
        "sales": _money(sales),
        "checks": checks,
        # средний чек — про продажи: возврат не должен его обнулять
        "avg_check": _money(sales / checks) if checks else _money(0),
        "cash": _money(cash),
        "card": _money(card),
        "online": _money(online),
        "refunds": refunds,
        "bonuses": _money(bonuses),
    }


def series(penalty_table: str, period: str, start, end, prev_start, prev_end) -> list[dict]:
    """Выручка по часам (сегодня) или по дням (неделя, месяц) рядом с прошлым окном.

    Прошлое окно раскладываем по тем же корзинам со сдвигом, поэтому на
    графике «вторник» стоит рядом с прошлым вторником. Корзины текущего
    окна — до его конца: будущие часы не рисуем нулями, а прошлые — целиком,
    чтобы было видно, куда день обычно идёт дальше.
    """
    hourly = period == "today"
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    if hourly:
        count = 24
    elif period == "week":
        count = 7
    else:
        # все дни месяца: так видно, сколько его ещё впереди
        nxt = (timezone.localdate(start).replace(day=28) + timedelta(days=4)).replace(day=1)
        count = (nxt - timezone.localdate(start)).days

    def bucket(qs, field: str, origin) -> dict[int, Decimal]:
        out: dict[int, Decimal] = {}
        for at, amount in qs.annotate(r=F("total") - F("bonus_spent")).values_list(field, "r"):
            local = timezone.localtime(at)
            if hourly:
                idx = local.hour if local.date() == timezone.localdate(origin) else -1
            else:
                idx = (local.date() - timezone.localdate(origin)).days
            if 0 <= idx < count:
                out[idx] = out.get(idx, Decimal("0")) + (amount or Decimal("0"))
        return out

    # прошлое окно берём целиком по корзинам — не обрезая «на это же время»
    prev_full_end = prev_start + step * count
    cur_sold = bucket(_sold(penalty_table, start, end), "paid_at", start)
    cur_back = bucket(_refunded(penalty_table, start, end), "refunded_at", start)
    prev_sold = bucket(_sold(penalty_table, prev_start, min(prev_full_end, start)), "paid_at", prev_start)
    prev_back = bucket(_refunded(penalty_table, prev_start, min(prev_full_end, start)), "refunded_at", prev_start)

    out = []
    for i in range(count):
        at = start + step * i
        future = at >= end
        out.append({
            "at": timezone.localtime(at).isoformat(),
            "value": None if future else _money(cur_sold.get(i, 0) - cur_back.get(i, 0)),
            "prev": _money(prev_sold.get(i, 0) - prev_back.get(i, 0)),
        })
    return out


def top_items(penalty_table: str, start, end) -> list[dict]:
    """Что продавалось лучше всего: по выручке, с количеством."""
    rows = (
        OrderItem.objects.filter(order__in=_sold(penalty_table, start, end))
        .values(
            "variant_id",
            "variant__product__name",
            "variant__label",
        )
        .annotate(qty=Sum("quantity"), sum=Sum(F("unit_price") * F("quantity")))
        .order_by("-sum", "-qty")[:TOP_LIMIT]
    )
    return [
        {
            "name": r["variant__product__name"],
            "variant": r["variant__label"] or "",
            "qty": r["qty"],
            "sum": _money(r["sum"]),
        }
        for r in rows
    ]


def attention(now: datetime, feats) -> list[dict]:
    """Что владельцу стоит сделать прямо сейчас. Пусто — всё в порядке.

    kind: danger — уже мешает работать, warn — скоро помешает, info — к сведению.
    """
    from core.license import status_payload
    from core.models import SiteSettings

    items: list[dict] = []

    lic = status_payload()
    paid_until = lic.get("paid_until")
    if not lic.get("internal"):
        st = lic.get("status")
        if st == "blocked":
            items.append({"kind": "danger", "key": "license",
                          "title": "Подписка остановлена",
                          "text": "Персонал не может работать, пока подписка не оплачена.",
                          "to": "/settings/subscription"})
        elif st == "grace":
            items.append({"kind": "danger", "key": "license",
                          "title": "Подписка просрочена",
                          "text": "Идут дни отсрочки — оплатите, чтобы точку не остановило.",
                          "to": "/settings/subscription"})
        elif st == "expiring" and paid_until:
            items.append({"kind": "warn", "key": "license",
                          "title": "Подписка скоро закончится",
                          "text": f"Оплачено до {paid_until[8:10]}.{paid_until[5:7]}.",
                          "to": "/settings/subscription"})

    site = SiteSettings.load()
    if site.ordering_paused:
        items.append({"kind": "warn", "key": "paused",
                      "title": "Заказы с сайта на паузе",
                      "text": "Гости видят меню, но заказать по QR не могут.",
                      "to": "/settings/ordering"})

    stuck_since = now - timedelta(minutes=STUCK_MINUTES)
    waiting = Order.objects.filter(
        status__in=(Order.Status.REQUESTED, Order.Status.AWAITING),
        created_at__lt=stuck_since,
    ).count()
    if waiting:
        items.append({"kind": "warn", "key": "waiting",
                      "title": f"Заказы ждут больше {STUCK_MINUTES} минут: {waiting}",
                      "text": "Гость ждёт официанта или оплата зависла на терминале.",
                      "to": "/orders"})

    if "inventory" in feats:
        from inventory.models import StockItem

        low = [s for s in StockItem.objects.filter(min_quantity__isnull=False) if s.is_low]
        if low:
            names = ", ".join(s.name for s in low[:3])
            more = f" и ещё {len(low) - 3}" if len(low) > 3 else ""
            items.append({"kind": "warn", "key": "stock",
                          "title": f"Заканчивается на складе: {len(low)}",
                          "text": names + more,
                          "to": "/warehouse"})

    today = _start_of(timezone.localdate(now))
    refunds = Order.objects.filter(
        status=Order.Status.REFUNDED, refunded_at__gte=today
    ).count()
    if refunds:
        items.append({"kind": "info", "key": "refunds",
                      "title": f"Возвраты сегодня: {refunds}",
                      "text": "Проверьте, всё ли вернули гостю и на склад.",
                      "to": "/orders"})
    return items


def summary(period: str, now: datetime | None = None) -> dict:
    from core.plans import features
    from shifts.models import ShiftSettings

    if period not in PERIODS:
        period = "today"
    now = now or timezone.now()
    cfg = ShiftSettings.load()
    penalty = cfg.penalty_table.name if cfg.penalty_table else ""
    start, end, prev_start, prev_end = windows(period, now)
    return {
        "period": period,
        "from": timezone.localtime(start).isoformat(),
        "to": timezone.localtime(end).isoformat(),
        "prev_from": timezone.localtime(prev_start).isoformat(),
        "prev_to": timezone.localtime(prev_end).isoformat(),
        "current": totals(penalty, start, end),
        "previous": totals(penalty, prev_start, prev_end),
        "series": series(penalty, period, start, end, prev_start, prev_end),
        "top": top_items(penalty, start, end),
        # прямо сейчас, вне периода: сколько заказов готовится
        "in_work": Order.objects.filter(status=Order.Status.OPEN).count(),
        "attention": attention(now, features()),
    }
