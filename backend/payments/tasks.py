"""Фоновая доводка онлайн-оплат.

Уведомление банка — не гарантия. Адрес для него прописывается в кабинете
банка руками и может быть не прописан вовсе; само уведомление теряется по
дороге; наш бэкенд мог в ту минуту перезапускаться. Любой из этих случаев
раньше оставлял заказ открытым при списанных у гостя деньгах.

Гостя, который держит страницу заказа открытой, выручает опрос из ручки
track. Но он мог закрыть вкладку сразу после оплаты — тогда заказ закроет
эта задача.
"""
from celery import shared_task


@shared_task
def settle_pending_payments_task():
    """Переспросить банк о незавершённых платежах всех заведений.

    Заведения обходим поимённо: модель платежей тенантная, и вне
    контекста заведения запрос к ней просто не соберётся. Ошибка у
    одного заведения не должна отменять доводку у остальных.
    """
    from core.models import Organization
    from core.tenancy import organization_context

    from .services import pending_online_payments, settle_payment

    report = {}
    for org in Organization.objects.all():
        with organization_context(org):
            settled = 0
            for payment in pending_online_payments():
                try:
                    settled += bool(settle_payment(payment))
                except Exception as exc:  # банк недоступен, доступы стёрли
                    report.setdefault(f"{org.slug}:ошибки", []).append(str(exc)[:200])
            if settled:
                report[org.slug] = settled
    return report
