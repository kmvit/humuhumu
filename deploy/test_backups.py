"""Linux host tests with a fake Docker executable; never connect to a real database."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class BackupScriptsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        docker = self.bin / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import base64, io, os, sys, tarfile
if sys.argv[1] == 'run':
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
        info = tarfile.TarInfo('photo.txt'); info.size = 5
        archive.addfile(info, io.BytesIO(b'photo'))
else:
    sys.stdout.buffer.write(base64.b64encode(os.urandom(4096)))
    sys.stdout.buffer.flush()
    sys.exit(int(os.environ.get('TEST_DUMP_FAIL', '0')))
''')
        docker.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH'],
                    'BACKUP_DIR': str(self.root / 'backups'), 'PROJECT_DIR': str(self.root),
                    'BACKUP_REQUEST_DIR': str(self.root / 'requests'), 'OFFSITE': '',
                    'KEEP_DB': '2', 'KEEP_MEDIA': '2'}

    def run_backup(self, *args):
        return subprocess.run(['bash', str(ROOT / 'backup.sh'), *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_successful_database_and_media_have_checksums(self):
        result = self.run_backup('--media')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        directory = Path(self.env['BACKUP_DIR'])
        self.assertEqual(len(list(directory.glob('*.gz'))), 2)
        for file in directory.glob('*.gz'):
            self.assertTrue(file.with_name(file.name + '.sha256').exists())
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_failed_dump_never_publishes_or_prunes(self):
        directory = Path(self.env['BACKUP_DIR'])
        directory.mkdir()
        previous = directory / 'db-20000101-000000.sql.gz'
        previous.write_text('keep')
        self.env['TEST_DUMP_FAIL'] = '1'
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(directory.glob('*.sql.gz')), [previous])
        self.assertFalse(list(directory.glob('.db-*')))

    def test_worker_records_success_and_failure(self):
        project = self.root / 'deploy'
        project.mkdir()
        script = project / 'backup.sh'
        queue = Path(self.env['BACKUP_REQUEST_DIR'])
        queue.mkdir()
        for exit_code in (0, 1):
            script.write_text(f'#!/bin/bash\nexit {exit_code}\n')
            (queue / 'request.json').write_text(json.dumps({'id': 'test', 'state': 'queued'}))
            result = subprocess.run(['python3', str(ROOT / 'backup-worker.py')],
                                    env=self.env, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, exit_code, result.stderr)
            status = json.loads((queue / 'status.json').read_text())
            self.assertEqual(status['state'], 'complete' if exit_code == 0 else 'failed')
            self.assertFalse((queue / 'request.json').exists())

    def test_restore_refuses_live_name_before_calling_docker(self):
        archive = self.root / 'db.sql.gz'
        archive.write_bytes(b'irrelevant')
        result = subprocess.run(['bash', str(ROOT / 'restore-db.sh'), str(archive), 'humu'],
                                env=self.env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
