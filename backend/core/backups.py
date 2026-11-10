"""Hub-only backup catalogue and a fixed-operation host queue."""
from datetime import datetime, timezone, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.views.decorators.http import require_POST

from .admin_site import is_platform_request

BACKUP_NAME = re.compile(r'^(db-\d{8}-\d{4,6}\.sql\.gz|media-\d{8}-\d{4,6}\.tar\.gz)$')


def _require_operator(request):
    if not is_platform_request(request):
        raise PermissionDenied


def catalogue():
    directory = Path(settings.BACKUP_DIR)
    copies = []
    if directory.exists():
        for path in directory.iterdir():
            if not BACKUP_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            info = path.stat()
            copies.append({'name': path.name, 'size': info.st_size,
                           'created_at': datetime.fromtimestamp(info.st_mtime, timezone.utc),
                           'kind': 'База данных' if path.name.startswith('db-') else 'Файлы',
                           'checksum': path.with_name(path.name + '.sha256').is_file()})
    return sorted(copies, key=lambda item: item['name'], reverse=True)


def queue_status():
    path = Path(settings.BACKUP_REQUEST_DIR) / 'status.json'
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def enqueue(user_id):
    directory = Path(settings.BACKUP_REQUEST_DIR)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / '.queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        status = queue_status()
        if (directory / 'request.json').exists() or status.get('state') in ('queued', 'running'):
            return False
        if status.get('finished_at'):
            finished = datetime.fromisoformat(status['finished_at'])
            if datetime.now(timezone.utc) - finished < timedelta(minutes=5):
                return False
        payload = {'id': uuid.uuid4().hex, 'requested_by': user_id,
                   'requested_at': datetime.now(timezone.utc).isoformat(), 'state': 'queued'}
        # Status first: a crash between writes is recovered by the host worker.
        for name in ('status.json', 'request.json'):
            temp = directory / (name + '.tmp')
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(payload, stream)
            temp.replace(directory / name)
        return True


def backup_index(request, site):
    _require_operator(request)
    copies = catalogue()
    databases = [item for item in copies if item['name'].startswith('db-')]
    stale = not databases or datetime.now(timezone.utc) - max(
        item['created_at'] for item in databases) > timedelta(hours=26)
    return TemplateResponse(request, 'admin/backups.html', {
        **site.each_context(request), 'title': 'Резервные копии',
        'copies': copies, 'backup_status': queue_status(), 'backup_stale': stale,
    })


@require_POST
def backup_create(request):
    _require_operator(request)
    try:
        queued = enqueue(request.user.pk)
    except OSError:
        messages.error(request, 'Не удалось поставить задачу. Проверьте настройку бэкапов на сервере.')
    else:
        if queued:
            messages.success(request, 'Создание копии поставлено в очередь. Обновите страницу через минуту.')
        else:
            messages.warning(request, 'Копия уже создаётся или была создана менее пяти минут назад.')
    return redirect('admin:backups')


def backup_download(request, filename):
    _require_operator(request)
    if not BACKUP_NAME.fullmatch(filename):
        raise Http404
    path = Path(settings.BACKUP_DIR) / filename
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise Http404 from exc
    response = FileResponse(os.fdopen(fd, 'rb'), as_attachment=True, filename=filename,
                            content_type='application/gzip')
    response['X-Content-Type-Options'] = 'nosniff'
    return response
