from celery import shared_task


@shared_task
def sync_license_task():
    """Суточная сверка с пультом «Падачи» (расписание в settings).

    Обходит все заведения установки: в общей базе их много, и у каждого
    свой ключ и своя подписка. Ошибка одного не отменяет сверку остальных.
    """
    from .license import sync_license
    from .models import Organization
    from .tenancy import organization_context

    report = {}
    for org in Organization.objects.all():
        with organization_context(org):
            try:
                state = sync_license()
                report[org.slug] = {"plan": state.plan, "paid_until": str(state.paid_until)}
            except Exception as exc:  # пульт недоступен, подпись не сошлась
                report[org.slug] = {"error": str(exc)[:200]}
    return report
