"""Корневая маршрутизация URL."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from catalog.views import CategoryViewSet, ProductViewSet
from core.views import SiteSettingsView
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
    # роутер (categories, products, token-packages, orders)
    *router.urls,
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(api_patterns)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
