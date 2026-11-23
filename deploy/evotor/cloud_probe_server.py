#!/usr/bin/env python3
"""Temporary loopback-only echo endpoint; headers are observations, not authentication."""
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from cloud_queue import Queue, QueueError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID


QUEUE = None


def bridge(action, body, auth='', *, host, venue=None):
    if not isinstance(host, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', host):
        raise QueueError(400, 'Некорректный адрес заведения')
    secret = os.environ.get('EVOTOR_CLOUD_BRIDGE_SECRET', '')
    if not secret:
        raise QueueError(503, 'Обмен с заказами ещё не включён')
    req = Request('http://127.0.0.1:18766/' + action + '/', data=json.dumps(body).encode(), headers={
        'Host': host, 'X-Cloud-Venue': str(venue or ''), 'Content-Type': 'application/json',
        'X-Cloud-Bridge': secret, 'Authorization': auth})
    try:
        with urlopen(req, timeout=4) as response:
            return json.load(response)
    except HTTPError as e:
        try:
            error = json.load(e)
            message = error.get('detail') if isinstance(error, dict) else '; '.join(map(str, error))
        except Exception:
            message = 'Не удалось обновить заказ'
        raise QueueError(e.code, message or 'Не удалось обновить заказ')
    except (URLError, ValueError):
        raise QueueError(503, 'Связь с заказами временно недоступна')


def sync_results():
    while True:
        try:
            for c in QUEUE.pending_results():
                try:
                    identity = QUEUE.venue_identity(c['venue'])
                    bridge('result', {'order_id': json.loads(c['payload'])['order_id'], 'id': c['id'], 'result': json.loads(c['result'])}, host=identity['host'], venue=c['venue'])
                    QUEUE.mark_synced(c['id'], c['result'])
                except QueueError as e:
                    QUEUE.mark_synced(c['id'], c['result'], e.message)
            for r in QUEUE.pending_refunds():
                try:
                    identity = QUEUE.venue_identity(r['venue'])
                    bridge('refund', {'result': json.loads(r['result'])}, host=identity['host'], venue=r['venue'])
                    QUEUE.mark_refund_synced(r)
                except QueueError as e:
                    QUEUE.mark_refund_synced(r, e.message)
        except Exception:
            pass  # Retained in SQLite; next iteration retries without losing a payment.
        time.sleep(2)



class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        pass  # Do not log URLs, bearer tokens or header values.

    def reply(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != '/api/evotor-cloud/panel/':
            self.reply(404, {'detail': 'Not found'})
            return
        raw = Path(__file__).with_name('cloud_panel.html').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(raw)

    def require_admin(self, allow_waiter=False):
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or len(auth) > 8192:
            raise QueueError(401, 'Войдите владельцем заведения')
        identity = bridge('identity', {}, auth, host=self.headers.get('Host', '').lower())
        allowed = ('admin', 'waiter') if allow_waiter else ('admin',)
        if identity.get('role') not in allowed:
            raise QueueError(403, 'Требуются права владельца заведения')
        return identity

    def do_POST(self):
        if self.path != '/api/evotor-cloud/probe/':
            try:
                prefix = '/api/evotor-cloud/'
                if not self.path.startswith(prefix) or not self.path.endswith('/'):
                    raise QueueError(404, 'Not found')
                action = self.path[len(prefix):-1]
                if action not in ('pair', 'next', 'result', 'refund', 'admin/pair', 'admin/status', 'admin/revoke', 'admin/command', 'admin/order', 'admin/order-status', 'admin/settings', 'admin/settings-save'):
                    raise QueueError(404, 'Not found')
                role, venue, host = None, None, None
                if action.startswith('admin/'):
                    identity = self.require_admin(allow_waiter=action in ('admin/order', 'admin/order-status', 'admin/status'))
                    venue = QUEUE.register_venue(identity)
                    role, host = identity['role'], identity['venue_host']
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 65536 or self.headers.get('Transfer-Encoding'):
                    raise QueueError(400, 'Некорректный размер запроса')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise QueueError(400, 'Ожидается JSON объект')
                if action == 'admin/settings-save' and body.get('device_id') and identity.get('other_kassa', 'none') != 'none':
                    raise QueueError(409, 'Сначала отключите прежнюю кассу в настройках оплаты, затем выберите Эвотор.')
                if action in ('admin/order', 'admin/order-status'):
                    auth = self.headers.get('Authorization', '')
                    if action == 'admin/order':
                        # Only a server-generated immutable snapshot can enter this queue.
                        settings = QUEUE.transact('internal/order-settings', {}, venue=venue)
                        transfer = bridge('prepare', {'order_id': body.get('order_id'), 'device_id': settings['device_id'], 'tax': settings['tax'], 'retry_of': body.get('retry_of')}, auth, host=host, venue=venue)
                        queued = QUEUE.transact('internal/order', {'id': transfer['id'], 'device_id': transfer['payload']['device_id'], 'kind': 'order_receipt', 'payload': transfer['payload']}, venue=venue)
                    else:
                        transfer = bridge('status', body, auth, host=host, venue=venue)
                    transfer['queue'] = QUEUE.command_status(transfer['id'], venue=venue) if transfer['id'] else None
                    self.reply(200, transfer)
                else:
                    if action == 'pair':
                        # This loopback-only service trusts the address overwritten by nginx, never JSON.
                        body['_client'] = self.headers.get('X-Real-IP', self.client_address[0])
                    reply = QUEUE.transact(action, body, venue=venue)
                    if action == 'admin/settings':
                        reply.update(venue_id=venue, venue_name=identity['venue_name'], venue_host=host)
                    if action == 'admin/status' and role == 'waiter':
                        reply = {'connected': reply['connected'], 'devices': [], 'commands': [c for c in reply['commands'] if c['kind'] == 'order_receipt'], 'refunds': reply['refunds']}
                    self.reply(200, reply)
            except QueueError as e:
                self.reply(e.status, {'detail': e.message})
            except (ValueError, TypeError):
                self.reply(400, {'detail': 'Некорректный JSON'})
            except Exception:
                # Never expose tokens, request bodies or SQL in logs/errors.
                self.reply(503, {'detail': 'Сервис временно недоступен'})
            return
        self.probe()

    def probe(self):
        if self.path != "/api/evotor-cloud/probe/":
            self.reply(404, {"detail": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 1024 or self.headers.get("Transfer-Encoding"):
                raise ValueError()
            body = json.loads(self.rfile.read(size))
            nonce = str(UUID(body["nonce"]))
            if body.get("protocol") != 1:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"detail": "Expected protocol=1 and UUID nonce"})
            return
        observed = {key: bool(self.headers.get(header)) for key, header in {
            "device": "X-Evotor-Device-UUID", "store": "X-Evotor-Store-Uuid",
            "user": "X-Evotor-User-Id", "authorization": "Authorization",
        }.items()}
        print(json.dumps({"nonce": nonce, "observed": observed}), flush=True)
        self.reply(200, {"service": "padacha-evotor-cloud-probe", "nonce": nonce,
                         "observed": observed, "diagnostic_only": True})


if __name__ == "__main__":
    state = Path(os.environ.get('STATE_DIRECTORY', '/tmp/padacha-evotor-cloud'))
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    QUEUE = Queue(state / 'queue.sqlite3')
    threading.Thread(target=sync_results, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 18765), Handler).serve_forever()
