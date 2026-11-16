from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from rest_framework.test import APITestCase
from catalog.models import Category, Product, ProductVariant, Modifier, ModifierGroup
from users.models import User
from .models import Order, OrderItem, OrderItemModifier
from .sales import sales_report


class SalesTests(APITestCase):
    def setUp(self):
        from core.models import Organization
        Organization.objects.first() or Organization.objects.create(slug="sales-test", name="Sales")
        category = Category.objects.create(name="Кофе")
        product = Product.objects.create(category=category, name="Латте")
        self.variant = ProductVariant.objects.create(product=product, price=200)
        self.now = timezone.now()

    def item(self, paid_at=None, **kwargs):
        order = Order.objects.create(paid_at=paid_at, **kwargs)
        return OrderItem.objects.create(order=order, variant=self.variant, quantity=2, unit_price=200)

    def test_sales_modifiers_unpaid_cancelled_and_refunds(self):
        item = self.item(self.now, status="paid")
        modifier = Modifier.objects.create(group=ModifierGroup.objects.create(name="Молоко"), name="Овсяное")
        OrderItemModifier.objects.create(modifier=modifier, order_item=item, name="Молоко", price_delta=30)
        self.item()
        self.item(self.now, status="cancelled")
        self.item(self.now - timedelta(days=3), status="refunded", refunded_at=self.now)
        result = sales_report(timezone.localdate(), timezone.localdate())
        self.assertEqual(result['totals'], dict(quantity=2, amount=Decimal(460), returned_quantity=2, returned_amount=Decimal(400)))

    def test_permissions_and_dates(self):
        user = User.objects.create_user(username="owner", role="admin")
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get('/api/orders/sales/?start=2026-11-16&end=2026-11-15').status_code, 400)
        self.assertEqual(self.client.get('/api/orders/sales/?start=2026-11-16&end=2026-11-16').status_code, 200)
        user.role = 'waiter'
        user.save()
        self.assertEqual(self.client.get('/api/orders/sales/').status_code, 403)
