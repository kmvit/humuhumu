from celery import shared_task


@shared_task
def sync_license_task():
    """Суточная сверка с пультом «Падачи» (расписание в settings)."""
    from .license import sync_license

    state = sync_license()
    return {"plan": state.plan, "paid_until": str(state.paid_until)}
