"""Корневая маршрутизация URL."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from catalog.views import CategoryViewSet, ProductViewSet
from core.branding import app_icon, manifest
from finance.views import ExpenseCategoryViewSet, ExpenseViewSet, PayrollViewSet
from core.views import SiteSettingsView, license_refresh, license_status
from loyalty import views as loyalty_views
from inventory.views import (
    PurchaseLineViewSet,
    PurchaseViewSet,
    ReceiptScanViewSet,
    ReceiptViewSet,
    RecipeViewSet,
    StockCategoryViewSet,
    StockItemAliasViewSet,
    StockItemViewSet,
)
from orders.views import OrderViewSet, TableViewSet
from billing.views import license_view
from payments.views import callback as payment_callback
from shifts.views import ShiftViewSet
from users.staff import StaffViewSet
from users.views import MeView, RegisterView
from wallet.views import (
    MyTransactionsView,
    MyWalletView,
    TokenPackageViewSet,
    TopupView,
)

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("products", ProductViewSet, basename="product")
router.register("token-packages", TokenPackageViewSet, basename="token-package")
router.register("orders", OrderViewSet, basename="order")
router.register("tables", TableViewSet, basename="table")
router.register("shifts", ShiftViewSet, basename="shift")
router.register("staff", StaffViewSet, basename="staff")
router.register("finance/payroll", PayrollViewSet, basename="finance-payroll")
router.register("finance/expenses", ExpenseViewSet, basename="finance-expense")
router.register(
    "finance/expense-categories",
    ExpenseCategoryViewSet,
    basename="finance-expense-category",
)
router.register("inventory/categories", StockCategoryViewSet, basename="stock-category")
router.register("inventory/items", StockItemViewSet, basename="stock-item")
router.register("inventory/aliases", StockItemAliasViewSet, basename="stock-alias")
router.register("inventory/receipts", ReceiptViewSet, basename="receipt")
router.register("inventory/receipt-scans", ReceiptScanViewSet, basename="receipt-scan")
router.register("inventory/recipes", RecipeViewSet, basename="recipe")
router.register("inventory/purchases", PurchaseViewSet, basename="purchase")
router.register(
    "inventory/purchase-lines", PurchaseLineViewSet, basename="purchase-line"
)

api_patterns = [
    # авторизация
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/register/", RegisterView.as_view(), name="register"),
    # сайт (публичные настройки)
    path("site/", SiteSettingsView.as_view(), name="site"),
    # пользователь
    path("users/me/", MeView.as_view(), name="me"),
    # кошелёк
    path("wallet/", MyWalletView.as_view(), name="my-wallet"),
    path("wallet/transactions/", MyTransactionsView.as_view(), name="my-transactions"),
    path("wallet/topup/", TopupView.as_view(), name="wallet-topup"),
    # бонусная программа
    path("loyalty/program/", loyalty_views.program, name="loyalty-program"),
    path("loyalty/enroll/", loyalty_views.EnrollView.as_view(), name="loyalty-enroll"),
    path("loyalty/lookup/", loyalty_views.lookup, name="loyalty-lookup"),
    path("loyalty/me/", loyalty_views.me, name="loyalty-me"),
    # выдача лицензии внешним установкам (кафе на своём сервере, демо)
    path("license/", license_view, name="license-issue"),
    # лицензия «Падачи»: статус подписки и ручная сверка с пультом
    path("license/status/", license_status, name="license-status"),
    path("license/refresh/", license_refresh, name="license-refresh"),
    # уведомления банка об оплате (без авторизации, подлинность — в провайдере)
    path(
        "payments/callback/<str:provider>/",
        payment_callback,
        name="payment-callback",
    ),
    # роутер (categories, products, token-packages, orders)
    *router.urls,
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(api_patterns)),
    # PWA: имя и иконка приложения — из настроек заведения, а не из сборки фронта.
    path("manifest.webmanifest", manifest, name="manifest"),
    path("app-icon-<int:size>.png", app_icon, name="app-icon"),
    path(
        "app-icon-<int:size>-maskable.png",
        app_icon,
        {"maskable": True},
        name="app-icon-maskable",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
