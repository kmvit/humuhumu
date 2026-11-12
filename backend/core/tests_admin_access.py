from django.contrib import admin
from django.test import TestCase, RequestFactory, override_settings

from core.models import Organization
from core.tenancy import organization_context
from users.models import User
from orders.models import Order


@override_settings(ALLOWED_HOSTS=['*'], ADMIN_HOST='hub.padacha.ru')
class AdminAuthorityTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.first()
        self.a.domain = 'monti.padacha.ru'
        self.a.save()
        self.b = Organization.objects.create(name='Demo', slug='demo', domain='demo.padacha.ru')
        with organization_context(self.a):
            self.owner = User.objects.create_superuser('cafe', password='password', role='admin')
            self.platform = User.objects.create_superuser(
                'platform', password='password', is_platform_admin=True)
            self.order_a = Order.objects.create()
        with organization_context(self.b):
            self.other = User.objects.create_superuser('demo', password='password')
            self.order_b = Order.objects.create()
        self.client.force_login(self.owner)

    def test_cafe_cannot_open_global_models_even_with_superuser(self):
        for path in ('core/organization', 'billing/client', 'billing/subscription',
                     'billing/payment', 'auth/group'):
            for suffix in ('/', '/add/'):
                with self.subTest(path=path, suffix=suffix):
                    self.assertEqual(self.client.get('/admin/' + path + suffix,
                        HTTP_HOST=self.a.domain).status_code, 403)
        html = self.client.get('/admin/', HTTP_HOST=self.a.domain).content.decode()
        self.assertNotIn('/admin/billing/', html)
        self.assertNotIn('/admin/core/organization/', html)

    def test_hub_rejects_cafe_superuser_and_login(self):
        response = self.client.get('/admin/', HTTP_HOST='hub.padacha.ru')
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        self.client.post('/admin/login/', {'username': 'cafe', 'password': 'password'},
                         HTTP_HOST='hub.padacha.ru')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_platform_login_and_switch(self):
        self.client.logout()
        response = self.client.post('/admin/login/',
            {'username': 'platform', 'password': 'password', 'next': '/admin/'},
            HTTP_HOST='hub.padacha.ru')
        self.assertEqual(response.status_code, 302)
        html = self.client.get('/admin/core/organization/', HTTP_HOST='hub.padacha.ru').content.decode()
        self.assertIn(self.a.domain, html)
        self.assertIn(self.b.domain, html)
        response = self.client.post('/admin/core/organization/', {
            'action': 'switch_to', '_selected_action': str(self.b.pk)}, HTTP_HOST='hub.padacha.ru')
        self.assertEqual(response.status_code, 302)
        html = self.client.get('/admin/orders/order/', HTTP_HOST='hub.padacha.ru').content.decode()
        self.assertIn(f'/{self.order_b.pk}/change/', html)
        self.assertNotIn(f'/{self.order_a.pk}/change/', html)
        self.assertEqual(self.client.get('/admin/billing/subscription/', HTTP_HOST='hub.padacha.ru').status_code, 200)

    def test_order_change_with_inline_opens_for_cafe_and_hub(self):
        response = self.client.get(
            f'/admin/orders/order/{self.order_a.pk}/change/', HTTP_HOST=self.a.domain)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'items-TOTAL_FORMS')
        self.client.force_login(self.platform)
        session = self.client.session
        session['tenant_override'] = self.b.pk
        session.save()
        response = self.client.get(
            f'/admin/orders/order/{self.order_b.pk}/change/', HTTP_HOST='hub.padacha.ru')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'items-TOTAL_FORMS')

    def test_cafe_cannot_switch_or_access_foreign_order(self):
        session = self.client.session
        session['tenant_override'] = self.b.pk
        session.save()
        html = self.client.get('/admin/orders/order/', HTTP_HOST=self.a.domain).content.decode()
        self.assertIn(f'/{self.order_a.pk}/change/', html)
        self.assertNotIn(f'/{self.order_b.pk}/change/', html)
        response = self.client.post(f'/admin/orders/order/{self.order_b.pk}/change/',
                                   {'status': 'closed'}, HTTP_HOST=self.a.domain)
        self.assertIn(response.status_code, (302, 403, 404))
        with organization_context(self.b):
            self.order_b.refresh_from_db()
            self.assertNotEqual(self.order_b.status, 'closed')
        self.assertEqual(self.client.get('/admin/', HTTP_HOST=self.b.domain).status_code, 302)

    def test_user_privileges_and_related_choices_are_protected(self):
        request = RequestFactory().get('/admin/', HTTP_HOST=self.a.domain)
        request.user = self.owner
        with organization_context(self.a):
            user_admin = admin.site._registry[User]
            form = user_admin.get_form(request, self.owner)
            for field in ('organization', 'is_superuser', 'is_staff', 'is_platform_admin', 'groups', 'user_permissions'):
                self.assertNotIn(field, form.base_fields)
            self.assertNotIn(self.platform, user_admin.get_queryset(request))
            order_form = admin.site._registry[Order].get_form(request)
            self.assertNotIn(self.other, order_form.base_fields['waiter'].queryset)
            self.assertNotIn(self.platform, order_form.base_fields['waiter'].queryset)
            self.assertNotIn('organization', order_form.base_fields)
        response = self.client.get(f'/admin/users/user/{self.platform.pk}/password/', HTTP_HOST=self.a.domain)
        self.assertIn(response.status_code, (302, 403, 404))

    def test_forged_user_post_cannot_grant_platform_rights_or_move_cafe(self):
        response = self.client.post(f'/admin/users/user/{self.owner.pk}/change/', {
            'username': self.owner.username, 'role': 'admin', 'is_active': 'on',
            'organization': self.b.pk, 'is_platform_admin': 'on',
            'is_staff': 'on', 'is_superuser': 'on',
            'date_joined_0': '2026-11-10', 'date_joined_1': '10:00:00',
            '_save': 'Save',
        }, HTTP_HOST=self.a.domain)
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_platform_admin)
        self.assertEqual(self.owner.organization_id, self.a.pk)

    def test_foreign_waiter_is_invalid_even_in_forged_form(self):
        request = RequestFactory().post('/admin/', HTTP_HOST=self.a.domain)
        request.user = self.owner
        with organization_context(self.a):
            field = admin.site._registry[Order].get_form(request).base_fields['waiter']
            from django.core.exceptions import ValidationError
            with self.assertRaises(ValidationError):
                field.clean(self.other.pk)

    def test_platform_grant_is_explicit_and_revocable(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('set_platform_admin', self.owner.pk, stdout=StringIO())
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_platform_admin)
        call_command('set_platform_admin', self.owner.pk, revoke=True, stdout=StringIO())
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_platform_admin)

    def test_old_global_actions_do_not_leak_on_cafe_index(self):
        from django.contrib.admin.models import LogEntry, CHANGE
        from django.contrib.contenttypes.models import ContentType
        LogEntry.objects.create(user=self.owner, action_flag=CHANGE,
            content_type=ContentType.objects.get_for_model(Order),
            object_id=str(self.order_b.pk), object_repr='SECRET FOREIGN ORDER')
        html = self.client.get('/admin/', HTTP_HOST=self.a.domain).content.decode()
        self.assertNotIn('SECRET FOREIGN ORDER', html)
