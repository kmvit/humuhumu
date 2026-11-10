from django.test import TestCase, override_settings
from core.models import Organization
from core.tenancy import organization_context
from users.models import User
from users.admin_forms import CafeUserChangeForm


@override_settings(ALLOWED_HOSTS=['*'], ADMIN_HOST='hub.padacha.ru')
class CafeAdminUserCreationTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.first()
        self.b = Organization.objects.create(name='Koko', domain='koko.padacha.ru', slug='koko')
        with organization_context(self.a):
            self.operator = User.objects.create_superuser('platform', password='secret', is_platform_admin=True)
            User.objects.create_user('owner', phone='+79990000001')
        self.client.force_login(self.operator)
        session = self.client.session
        session['tenant_override'] = self.b.pk
        session.save()

    def create(self, username):
        return self.client.post('/admin/users/user/add/', {
            'username': username, 'password1': 'Koko-test-strong-987!',
            'password2': 'Koko-test-strong-987!', '_save': 'Save',
        }, HTTP_HOST='hub.padacha.ru')

    def test_same_login_in_other_cafe_is_allowed(self):
        response = self.create('owner')
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(organization=self.b, username='owner')
        self.assertTrue(user.check_password('Koko-test-strong-987!'))
        self.assertFalse(user.is_platform_admin)
        self.assertEqual(User.objects.filter(username='owner').count(), 2)

    def test_duplicate_in_same_cafe_is_form_error_including_case(self):
        with organization_context(self.b):
            User.objects.create_user('owner')
        for username in ('owner', 'OWNER'):
            response = self.create(username)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'уже существует в этом заведении')
        self.assertEqual(User.objects.filter(organization=self.b).count(), 1)

    def test_selected_cafe_is_shown_before_creation(self):
        response = self.client.get('/admin/users/user/add/', HTTP_HOST='hub.padacha.ru')
        self.assertContains(response, 'Koko')

    def test_edit_scopes_username_and_phone_and_excludes_self(self):
        with organization_context(self.b):
            user = User.objects.create_user('local', phone='+79990000002')
            form = CafeUserChangeForm(instance=user)
            form.cleaned_data = {'username': 'owner', 'phone': '+79990000001'}
            self.assertEqual(form.clean_username(), 'owner')
            self.assertEqual(form.clean_phone(), '+79990000001')
            form.cleaned_data = {'username': 'local', 'phone': user.phone}
            self.assertEqual(form.clean_username(), 'local')
            self.assertEqual(form.clean_phone(), user.phone)
