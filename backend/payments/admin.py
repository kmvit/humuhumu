from django.contrib import admin

from .acquiring import acquirer_class
from .models import AcquiringCredentials, Payment
from .providers import provider_class


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id", "purpose", "status", "amount", "method", "provider", "order",
        "fiscal_receipt", "created_at",
    )
    list_filter = ("purpose", "status", "provider", "method")
    search_fields = ("external_id", "order__id", "fiscal_receipt")
    readonly_fields = ("external_id",)
    raw_id_fields = ("order",)


@admin.register(AcquiringCredentials)
class AcquiringCredentialsAdmin(admin.ModelAdmin):
    """Только посмотреть и при нужде стереть.

    Править доступы отсюда нельзя намеренно: в базе лежит шифр, а вводить
    ключи владелец должен у себя в разделе «Оплата картой». Поддержке же
    нужно ровно одно — видеть, заданы они или нет, когда кафе звонит с
    «оплата не работает».
    """

    list_display = ("organization", "provider", "filled", "updated_at")
    list_filter = ("provider",)
    readonly_fields = ("organization", "provider", "filled", "updated_at")
    exclude = ("payload",)

    @admin.display(description="Заданные поля")
    def filled(self, obj) -> str:
        stored = obj.values()
        # В этой таблице лежат и ключи банков, и ключи касс: поля кассы
        # описывает её драйвер. Без этого ключ aQsi показывался «пустым»,
        # хотя касса работала, — и поддержка искала поломку не там.
        cls = provider_class(obj.provider) or acquirer_class(obj.provider)
        names = [f.label for f in cls.fields if stored.get(f.key)]
        return ", ".join(names) or "— (пусто или не расшифровываются)"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
