from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib import admin
from django.test import Client, TestCase, override_settings

from core.backups import catalogue, enqueue, queue_status
from core.models import Organization
from core.tenancy import organization_context
from users.models import User


@override_settings(ALLOWED_HOSTS=['*'], ADMIN_HOST='hub.padacha.ru')
class BackupAdminTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive_dir = Path(self.temp.name) / 'backups'
        self.archive_dir.mkdir()
        self.settings_override = override_settings(BACKUP_DIR=str(self.archive_dir),
            BACKUP_REQUEST_DIR=str(Path(self.temp.name) / 'requests'))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.org = Organization.objects.first()
        self.org.domain = 'monti.padacha.ru'
        self.org.save()
        Organization.objects.create(name='Second', slug='second', domain='demo.padacha.ru')
        with organization_context(self.org):
            self.cafe = User.objects.create_superuser('cafe', password='test')
            self.operator = User.objects.create_superuser('operator', password='test', is_platform_admin=True)
        self.archive = self.archive_dir / 'db-20261110-031700.sql.gz'
        self.archive.write_bytes(b'backup bytes')
        self.client.force_login(self.operator)

    def test_operator_list_download_and_queue(self):
        response = self.client.get('/admin/backups/', HTTP_HOST='hub.padacha.ru')
        self.assertContains(response, self.archive.name)
        response = self.client.get('/admin/backups/download/' + self.archive.name + '/', HTTP_HOST='hub.padacha.ru')
        self.assertEqual(b''.join(response.streaming_content), b'backup bytes')
        response = self.client.post('/admin/backups/create/', HTTP_HOST='hub.padacha.ru')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(queue_status()['state'], 'queued')
        self.assertFalse(enqueue(self.operator.pk))

    def test_cafe_and_operator_on_cafe_domain_cannot_access_backups(self):
        for user in (self.cafe, self.operator):
            self.client.force_login(user)
            for path in ('/admin/backups/', '/admin/backups/download/' + self.archive.name + '/'):
                self.assertEqual(self.client.get(path, HTTP_HOST=self.org.domain).status_code, 403)
            self.assertEqual(self.client.post('/admin/backups/create/', HTTP_HOST=self.org.domain).status_code, 403)
        self.assertEqual(queue_status(), {})

    def test_cafe_cannot_use_hub_and_link_is_hidden(self):
        self.client.force_login(self.cafe)
        self.assertEqual(self.client.get('/admin/backups/', HTTP_HOST='hub.padacha.ru').status_code, 302)
        response = self.client.get('/admin/', HTTP_HOST=self.org.domain)
        self.assertNotContains(response, '/admin/backups/')

    def test_get_and_csrf_cannot_create_backup(self):
        self.assertEqual(self.client.get('/admin/backups/create/', HTTP_HOST='hub.padacha.ru').status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        self.assertEqual(client.post('/admin/backups/create/', HTTP_HOST='hub.padacha.ru').status_code, 403)
        self.assertEqual(queue_status(), {})

    def test_partial_and_symlink_files_are_not_served(self):
        secret = Path(self.temp.name) / 'secret'
        secret.write_text('not a backup')
        link = self.archive_dir / 'db-20261110-042000.sql.gz'
        link.symlink_to(secret)
        (self.archive_dir / '.db-partial').write_text('partial')
        self.assertEqual([row['name'] for row in catalogue()], [self.archive.name])
        for name in (link.name, '.db-partial', 'secret', '..'):
            response = self.client.get('/admin/backups/download/' + name + '/', HTTP_HOST='hub.padacha.ru')
            self.assertEqual(response.status_code, 404)

    def test_queue_failure_is_reported_without_exposing_exception(self):
        with patch('core.backups.enqueue', side_effect=OSError('secret path')):
            response = self.client.post('/admin/backups/create/', HTTP_HOST='hub.padacha.ru', follow=True)
        self.assertContains(response, 'Не удалось поставить задачу')
        self.assertNotContains(response, 'secret path')
