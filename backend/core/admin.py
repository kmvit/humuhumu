from django.contrib import admin, messages
from django.utils.html import format_html

from .models import LicenseState, Organization, SiteSettings
from .tenancy import current_organization_or_none


class TenantAdminMixin:
    """Показывать в админке только текущее заведение.

    Нужен там, где менеджер модели намеренно НЕ фильтрует: у User на нём
    держится вход и createsuperuser, у настроек — загрузка по заведению.
    Без этого админка на домене одного кафе показывала сотрудников всех —
    ровно та утечка, ради предотвращения которой всё и затевалось.

    Моделям на TenantModel он не нужен: их менеджер фильтрует сам.
    """

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        org = current_organization_or_none()
        return qs.filter(organization=org) if org is not None else qs


@admin.register(SiteSettings)
class SiteSettingsAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Все настройки заведения — здесь.

    Продукт разворачивается разным кафе, и каждое поле кто-то должен
    заполнить руками при подключении. Поле, которого нет в fieldsets,
    правится только через shell — то есть для заказчика не существует.
    За полнотой следит тест core.tests.AdminCoversModelTests.
    """

    fieldsets = (
        ("Основное", {"fields": ("name", "tagline", "app_short_name", "logo", "about")}),
        (
            "Тариф",
            {
                "fields": ("plan",),
                "description": (
                    "Что включено заведению по тарифной сетке «Падачи». "
                    "Если в .env задан LICENSE_KEY, поле перезаписывается "
                    "лицензией из пульта при суточной сверке — править его "
                    "тогда нужно в пульте, а не здесь."
                ),
            },
        ),
        ("Внешний вид", {"fields": ("theme", "accent_color", "dark_by_default")}),
        (
            "Работа заведения",
            {
                "fields": ("service_mode", "item_remove_code"),
                "description": "Формат обслуживания и права официанта на удаление позиций.",
            },
        ),
        (
            "Бонусная программа",
            {
                "fields": (
                    "bonus_enabled",
                    "bonus_welcome",
                    "bonus_earn_percent",
                    "bonus_redeem_waiter",
                    "bonus_redeem_guest",
                ),
                "description": (
                    "1 бонус = 1 ₽. Доступна на тарифе «Максимум». "
                    "Владельцу удобнее править это в разделе «Бонусы» "
                    "на фронте — здесь те же настройки."
                ),
            },
        ),
        ("Контакты", {"fields": ("phone", "email", "address", "working_hours")}),
        ("Соцсети", {"fields": ("instagram", "telegram")}),
        (
            "Приём оплаты",
            {
                "fields": ("acquiring", "online_payment_on"),
                "description": (
                    "Кто принимает оплату картой онлайн. Ключи и пароли банка "
                    "задаются переменными окружения на сервере заведения "
                    "(TBANK_* или SBER_*), в базе их не храним."
                ),
            },
        ),
        (
            "Реквизиты",
            {
                "fields": (
                    "merchant_type",
                    "merchant_name",
                    "merchant_short",
                    "merchant_address",
                    "merchant_inn",
                    "merchant_ogrn",
                ),
                "description": "Показываются на юридических страницах и в подвале.",
            },
        ),
        (
            "Банковские реквизиты",
            {
                "classes": ("collapse",),
                "fields": (
                    "merchant_account",
                    "merchant_bank",
                    "merchant_bank_inn",
                    "merchant_bik",
                    "merchant_corr_account",
                    "merchant_bank_address",
                ),
            },
        ),
        (
            "Юридические страницы",
            {
                "classes": ("collapse",),
                "fields": ("acquirer", "legal_updated"),
            },
        ),
    )

    def has_add_permission(self, request):
        # запись одна НА ЗАВЕДЕНИЕ — новую не создаём, если у него уже есть
        org = current_organization_or_none()
        qs = SiteSettings.objects.filter(organization=org) if org else SiteSettings.objects
        return not qs.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LicenseState)
class LicenseStateAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Кэш лицензии — только посмотреть. Меняет его сверка с пультом."""

    readonly_fields = (
        "plan", "paid_until", "grace_days", "issued_at", "checked_at", "last_error",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    """Главная страница «Падачи»: все заведения установки.

    Здесь владелец продукта видит парк целиком и может открыть любое
    заведение — кнопка «Работать от имени» подменяет текущее заведение
    для всей админки, не требуя заходить на его домен.
    """

    list_display = (
        "name",
        "domain",
        "is_active",
        "current_badge",
        "orders_count",
        "last_order",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "domain", "slug")
    prepopulated_fields = {"slug": ("name",)}
    actions = ["switch_to"]
    fieldsets = (
        (None, {"fields": ("name", "slug", "domain", "is_active")}),
        (
            "Подписка",
            {
                "fields": ("license_key",),
                "description": (
                    "Ключ из пульта padacha.ru/pult/. Пусто — заведение не "
                    "биллится (своя точка или отдельная установка)."
                ),
            },
        ),
    )

    # Считаем прямыми запросами, а не annotate: у тенантной связи нет
    # обратного имени (related_name="+"), да и заведений десятки — на
    # списке это незаметно.
    @admin.display(description="Заказов")
    def orders_count(self, obj):
        from orders.models import Order

        return Order.all_objects.filter(organization=obj).count()

    @admin.display(description="Последний заказ")
    def last_order(self, obj):
        from orders.models import Order

        last = (
            Order.all_objects.filter(organization=obj)
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        return last.strftime("%d.%m.%Y %H:%M") if last else "—"

    @admin.display(description="Открыто")
    def current_badge(self, obj):
        from .tenancy import current_organization

        if current_organization() == obj:
            return format_html('<b style="color:#1a7f37">сейчас здесь</b>')
        return ""

    @admin.action(description="Работать от имени этого заведения")
    def switch_to(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request, "Выберите ровно одно заведение", level=messages.WARNING
            )
            return
        org = queryset.first()
        request.session["tenant_override"] = org.pk
        self.message_user(request, f"Админка открыта от имени «{org.name}»")
