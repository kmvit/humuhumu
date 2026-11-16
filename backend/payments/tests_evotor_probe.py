import uuid
from datetime import timedelta

from django.utils import timezone
from django.test import override_settings
from rest_framework.test import APITestCase
from core.models import Organization
from users.models import User
from .models import EvotorProbeCommand, EvotorProbeDevice, EvotorProbePairing, Payment


class EvotorProbeTests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.first()
        if self.org is None:
            self.org = Organization.objects.create(name="Проверка", slug="probe")
        self.admin = User.objects.create_user(username="probe-owner", password="test", role="admin", organization=self.org)
        device = self.enroll()
        self.device, self.token = str(device['id']), device['token']
        self.url = f'/api/evotor-probe/devices/{self.device}/commands/'

    def enroll(self):
        self.client.force_authenticate(self.admin)
        issued = self.client.post('/api/evotor-probe/devices/', {"name": "Касса"}, format='json')
        self.assertEqual(issued.status_code, 201, issued.data)
        self.assertNotIn('token', issued.data)
        self.client.force_authenticate(None)
        self.client.credentials()
        claimed = self.client.post('/api/evotor-probe/pair/', {
            "code": issued.data['code'], "client_nonce": uuid.uuid4().hex + uuid.uuid4().hex,
        }, format='json')
        self.assertEqual(claimed.status_code, 200, claimed.data)
        self.client.force_authenticate(self.admin)
        return claimed.data

    def command(self, kind='echo', identifier=None):
        self.client.force_authenticate(self.admin)
        return self.client.post(self.url, {"id": str(identifier or uuid.uuid4()), "kind": kind}, format='json')

    def as_device(self, token=None):
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='EvotorProbe ' + (token or self.token))

    def test_roundtrip_is_idempotent_and_cannot_pay(self):
        identifier = uuid.uuid4()
        self.assertEqual(self.command(identifier=identifier).status_code, 201)
        self.assertEqual(self.command(identifier=identifier).status_code, 200)
        self.as_device()
        a = self.client.post('/api/evotor-probe/next/', {}, format='json')
        b = self.client.post('/api/evotor-probe/next/', {}, format='json')
        self.assertEqual(a.data['id'], b.data['id'])
        self.assertTrue(a.data['test_only'])
        url = f'/api/evotor-probe/commands/{identifier}/result/'
        self.assertEqual(self.client.post(url, {"status": "succeeded"}, format='json').status_code, 400)
        for _ in range(2):
            self.assertEqual(self.client.post(url, {"status": "echo_ok"}, format='json').status_code, 200)
        self.assertEqual(self.client.post(url, {"status": "error"}, format='json').status_code, 409)
        self.assertEqual(self.client.post('/api/evotor-probe/next/', {}, format='json').status_code, 204)
        self.assertEqual(Payment.objects.count(), 0)

    def test_busy_and_expiry(self):
        r = self.command()
        self.assertEqual(self.command().status_code, 409)
        EvotorProbeCommand.objects.filter(pk=r.data['id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.as_device()
        self.assertEqual(self.client.post('/api/evotor-probe/next/', {}, format='json').status_code, 204)
        self.assertEqual(EvotorProbeCommand.objects.get(pk=r.data['id']).status, 'expired')

    def test_received_command_not_requeued_on_timeout(self):
        r = self.command('open_receipt')
        self.as_device()
        self.client.post('/api/evotor-probe/next/', {}, format='json')
        EvotorProbeCommand.objects.filter(pk=r.data['id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.post('/api/evotor-probe/next/', {}, format='json').data['status'], 'received')
        self.assertEqual(self.command().status_code, 409)

    def test_revocation_and_staff_permissions(self):
        self.client.delete(f'/api/evotor-probe/devices/{self.device}/')
        self.as_device()
        self.assertEqual(self.client.post('/api/evotor-probe/next/', {}, format='json').status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.get('/api/evotor-probe/devices/').status_code, 401)
        waiter = User.objects.create_user(username='probe-waiter', role='waiter', organization=self.org)
        self.client.force_authenticate(waiter)
        self.assertEqual(self.client.get('/api/evotor-probe/devices/').status_code, 403)

    def test_device_cannot_call_staff_api_or_report_another_device(self):
        r = self.command()
        other = self.enroll()
        self.as_device(other['token'])
        self.assertEqual(self.client.get('/api/evotor-probe/devices/').status_code, 401)
        self.assertEqual(self.client.post(f'/api/evotor-probe/commands/{r.data["id"]}/result/', {"status":"echo_ok"}, format='json').status_code, 404)

    @override_settings(ALLOWED_HOSTS=["testserver", "probe.example", "other.example"])
    def test_token_is_hashed_and_tenant_bound(self):
        self.assertNotEqual(EvotorProbeDevice.objects.get(pk=self.device).token_hash, self.token)
        self.org.domain = 'probe.example'
        self.org.save()
        other = Organization.objects.create(name='Другое кафе', slug='other', domain='other.example')
        self.as_device()
        response = self.client.post('/api/evotor-probe/next/', {}, format='json', HTTP_HOST='other.example')
        self.assertEqual(response.status_code, 401)

    def test_result_must_match_received_kind(self):
        r = self.command('open_receipt')
        self.as_device()
        url = f'/api/evotor-probe/commands/{r.data["id"]}/result/'
        self.assertEqual(self.client.post(url, {"status":"receipt_opened"}, format='json').status_code, 409)
        self.client.post('/api/evotor-probe/next/', {}, format='json')
        self.assertEqual(self.client.post(url, {"status":"echo_ok"}, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, {"status":"receipt_opened", "receipt_uuid":"test"}, format='json').status_code, 200)


class EvotorPairingTests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.first() or Organization.objects.create(name="Тест", slug="pair")
        self.admin = User.objects.create_user(username="pair-owner", password="test", role="admin", organization=self.org)

    def issue(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/evotor-probe/devices/', {}, format='json')
        self.assertEqual(response.status_code, 201)
        self.client.force_authenticate(None)
        return response.data

    def claim(self, code, nonce='a' * 64):
        return self.client.post('/api/evotor-probe/pair/', {'code': code, 'client_nonce': nonce}, format='json')

    def test_one_installation_and_retry_after_lost_response(self):
        p = self.issue()
        self.assertRegex(p['code'], r'^[0-9]{6}$')
        self.assertNotIn('token', p)
        a = self.claim(p['code'])
        self.assertEqual(a.status_code, 200)
        self.assertEqual(self.claim(p['code']).data, a.data)
        self.assertEqual(self.claim(p['code'], 'b' * 64).status_code, 400)
        self.assertEqual(EvotorProbeDevice.objects.count(), 1)
        self.client.force_authenticate(self.admin)
        status = self.client.get(f"/api/evotor-probe/pairing/{p['pairing_id']}/")
        self.assertEqual(status.data['status'], 'connected')
        self.assertNotIn('token', status.data)

    def test_five_wrong_attempts_lock_code(self):
        p = self.issue()
        wrong = '000000' if p['code'] != '000000' else '111111'
        for _ in range(5):
            self.assertEqual(self.claim(wrong).status_code, 400)
        self.assertEqual(self.claim(p['code']).status_code, 400)
        self.assertEqual(EvotorProbeDevice.objects.count(), 0)

    def test_expiry_and_new_code_invalidate_old(self):
        from unittest.mock import patch
        with patch('payments.evotor_probe.secrets.randbelow', return_value=123456):
            old = self.issue()
        with patch('payments.evotor_probe.secrets.randbelow', return_value=654321):
            new = self.issue()
        self.assertEqual(self.claim(old['code']).status_code, 400)
        EvotorProbePairing.objects.filter(pk=new['pairing_id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.claim(new['code']).status_code, 400)

    def test_leading_zero_and_revocation(self):
        from unittest.mock import patch
        with patch('payments.evotor_probe.secrets.randbelow', return_value=42):
            p = self.issue()
        self.assertEqual(p['code'], '000042')
        claimed = self.claim(p['code'])
        self.assertEqual(claimed.status_code, 200)
        self.client.force_authenticate(self.admin)
        self.client.delete(f"/api/evotor-probe/devices/{claimed.data['id']}/")
        self.client.force_authenticate(None)
        self.assertEqual(self.claim(p['code']).status_code, 400)

    @override_settings(ALLOWED_HOSTS=['testserver', 'other.example'])
    def test_code_is_tenant_scoped_and_issue_requires_owner(self):
        p = self.issue()
        self.org.domain = 'testserver'
        self.org.save()
        Organization.objects.create(name='Other', slug='other-pair', domain='other.example')
        r = self.client.post('/api/evotor-probe/pair/', {'code':p['code'], 'client_nonce':'a'*64}, format='json', HTTP_HOST='other.example')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.post('/api/evotor-probe/devices/', {}, format='json').status_code, 401)
        self.assertEqual(self.claim(p['code']).status_code, 200)
