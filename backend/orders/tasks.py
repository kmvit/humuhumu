"""Фоновые задачи заказов.

Пока одна: неоплаченные заказы стойки не живут вечно. Гость собрал
корзину, ушёл со страницы банка и не вернулся — такой заказ висит у
баристы в колонке «Ждут оплаты» и портит статистику дня. За смену их
набегают десятки.
"""
from celery import shared_task

#: Сколько ждём оплату. Четверть часа — это и «отошёл к банкомату», и
#: «долго вводил карту», но уже не «передумал ещё у двери».
STALE_AFTER_MINUTES = 15


@shared_task
def cancel_stale_unpaid_orders_task():
    """Отменить заказы, которые так и не оплатили.

    Перед отменой спрашиваем банк о незавершённых платежах заказа: гость
    мог заплатить в последнюю минуту, а уведомление — потеряться. Отменить
    оплаченный заказ куда хуже, чем подержать лишние полминуты.
    """
    from datetime import timedelta

    from django.utils import timezone

    from core.models import Organization
    from core.tenancy import organization_context
    from payments.services import settle_order

    from .models import Order

    report = {}
    edge = timezone.now() - timedelta(minutes=STALE_AFTER_MINUTES)
    for org in Organization.objects.all():
        with organization_context(org):
            cancelled = 0
            for order in Order.objects.filter(status=Order.Status.UNPAID, created_at__lt=edge):
                settle_order(order)
                order.refresh_from_db()
                if order.status != Order.Status.UNPAID:
                    continue  # успел оплатить
                order.status = Order.Status.CANCELLED
                order.closed_at = timezone.now()
                order.save(update_fields=["status", "closed_at"])
                cancelled += 1
            if cancelled:
                report[org.slug] = cancelled
    return report
