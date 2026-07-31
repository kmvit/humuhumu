from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_wallet_for_client(sender, instance, created, **kwargs):
    """Автоматически заводим кошелёк при создании пользователя-клиента."""
    # при загрузке фикстур (loaddata) кошельки приходят из данных — сигнал не вмешивается
    if kwargs.get("raw") or not created:
        return
    from wallet.models import Wallet

    Wallet.objects.get_or_create(user=instance)
