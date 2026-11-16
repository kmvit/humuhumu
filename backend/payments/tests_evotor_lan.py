import uuid
from decimal import Decimal
from django.test import override_settings
from rest_framework.test import APITestCase
from core.models import Organization
from users.models import User
from catalog.models import Category, Product, ProductVariant
from orders.models import Order, OrderItem
from .models import EvotorLanTransfer, Payment

@override_settings(EVOTOR_LAN_HOSTS=["testserver", "other-lan.example"])
class EvotorLanTests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.first() or Organization.objects.create(name='LAN', slug='lan')
        self.user = User.objects.create_user(username='lan-waiter', role='waiter', organization=self.org)
        self.client.force_authenticate(self.user)
        category = Category.objects.create(name='Кофе', organization=self.org)
        product = Product.objects.create(name='Капучино', category=category, organization=self.org)
        self.variant = ProductVariant.objects.create(product=product, price='123.45', organization=self.org)
        self.order = Order.objects.create(total='246.90', organization=self.org)
        self.item = OrderItem.objects.create(order=self.order, variant=self.variant, unit_price='123.45', quantity=2, organization=self.org)
        self.url = f'/api/orders/{self.order.pk}/evotor-lan/'
        self.body = {'tax': 'NO_VAT', 'device_id': str(uuid.uuid4())}

    def prepare(self):
        r = self.client.post(self.url, self.body, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def test_exact_snapshot_retry_and_result_never_pay(self):
        a = self.prepare()
        self.assertEqual(a, self.prepare())
        self.assertEqual(a['payload']['positions'][0]['price'], '123.45')
        self.assertEqual(a['payload']['positions'][0]['quantity'], 2)
        self.assertEqual(a['payload']['total'], '246.90')
        result = dict(status='receipt_opened', message='Открыт', receipt_uuid='receipt')
        for _ in range(2):
            r = self.client.post(self.url, {'id': a['id'], 'result': result}, format='json')
            self.assertEqual(r.status_code, 200, r.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.OPEN)
        self.assertIsNone(self.order.paid_at)
        self.assertFalse(Payment.objects.exists())

    def test_modified_order_or_device_cannot_create_second_receipt(self):
        self.prepare()
        changed = {**self.body, 'device_id': str(uuid.uuid4())}
        self.assertEqual(self.client.post(self.url, changed, format='json').status_code, 400)
        self.item.quantity = 3; self.item.save()
        self.order.total = Decimal('370.35'); self.order.save()
        self.assertEqual(self.client.post(self.url, self.body, format='json').status_code, 400)
        self.assertEqual(EvotorLanTransfer.objects.count(), 1)

    def test_unsupported_bonus_tax_and_inconsistent_total(self):
        self.assertEqual(self.client.post(self.url, {**self.body, 'tax': 'guess'}, format='json').status_code, 400)
        self.order.bonus_spent = 1; self.order.save()
        self.assertEqual(self.client.post(self.url, self.body, format='json').status_code, 400)
        self.order.bonus_spent = 0; self.order.total = 1; self.order.save()
        self.assertEqual(self.client.post(self.url, self.body, format='json').status_code, 400)
        self.assertFalse(EvotorLanTransfer.objects.exists())

    def test_permissions_and_result_status(self):
        a = self.prepare()
        self.assertEqual(self.client.post(self.url, {'id': a['id'], 'result': {'status': 'paid'}}, format='json').status_code, 400)
        other = User.objects.create_user(username='lan-cook', role='cook', organization=self.org)
        self.client.force_authenticate(other)
        self.assertEqual(self.client.post(self.url, self.body, format='json').status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.url, self.body, format='json').status_code, 401)

    def test_retryable_rejection_can_be_replaced_but_final_cannot(self):
        a = self.prepare()
        for result in [dict(status='error', retryable=True, message='Чек занят'), dict(status='receipt_opened', message='Открыт')]:
            self.assertEqual(self.client.post(self.url, {'id':a['id'], 'result':result}, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url, {'id':a['id'], 'result':dict(status='error')}, format='json').status_code, 400)

    @override_settings(ALLOWED_HOSTS=['testserver', 'other-lan.example'])
    def test_tenant_isolation(self):
        self.org.domain = 'testserver'; self.org.save()
        Organization.objects.create(name='Other', slug='other-lan', domain='other-lan.example')
        r = self.client.post(self.url, self.body, format='json', HTTP_HOST='other-lan.example')
        self.assertIn(r.status_code, (403, 404))

    @override_settings(EVOTOR_LAN_HOSTS=[])
    def test_disabled_tenant(self):
        self.assertEqual(self.client.post(self.url, self.body, format='json').status_code, 403)

    def test_invalid_result_id(self):
        self.assertEqual(self.client.post(self.url, {'id':'oops', 'result':{'status':'error'}}, format='json').status_code, 400)
