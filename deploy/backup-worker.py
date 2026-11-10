#!/usr/bin/env python3
"""Host-side queue: never expose the Docker socket or command execution to Django."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
from datetime import datetime, timezone

queue = Path(os.environ.get('BACKUP_REQUEST_DIR', '/opt/padacha-backup-requests'))
queue.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(queue, 0o700)
os.umask(0o077)


def write_status(value):
    temp = queue / 'status.tmp'
    temp.write_text(json.dumps(value))
    temp.replace(queue / 'status.json')


with (queue / '.worker.lock').open('a') as worker_lock:
    try:
        fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    with (queue / '.queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        request = queue / 'request.json'
        if not request.exists():
            status_file = queue / 'status.json'
            if status_file.exists():
                state = json.loads(status_file.read_text())
                if state.get('state') in ('running', 'queued'):
                    state.update(state='failed', message='Процесс прерван. Создайте новую копию.')
                    write_status(state)
            raise SystemExit(0)
        state = json.loads(request.read_text())
        state.update(state='running', started_at=datetime.now(timezone.utc).isoformat())
        write_status(state)
        request.unlink()
    script = Path(os.environ.get('PROJECT_DIR', '/opt/padacha-app')) / 'deploy/backup.sh'
    log_path = queue / 'last-run.log'
    with log_path.open('w') as log:
        process = subprocess.Popen(['bash', str(script)], stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            result = process.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            result = 124
    state.update(state='complete' if result == 0 else 'failed',
                 finished_at=datetime.now(timezone.utc).isoformat(),
                 message='Копия создана.' if result == 0 else
                         'Ошибка резервного копирования. Проверьте журнал на сервере.')
    with (queue / '.queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        write_status(state)
    raise SystemExit(result)
