"""Tenant-isolated durable queue. Order snapshots come only from the authenticated demo bridge."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid


class QueueError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identifier(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        raise QueueError(400, 'Некорректный идентификатор')


class Queue:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS venues(id TEXT PRIMARY KEY, host TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pairing_limits(client TEXT PRIMARY KEY, started REAL, attempts INTEGER);
            CREATE TABLE IF NOT EXISTS refunds(device TEXT NOT NULL, receipt TEXT NOT NULL,
              result TEXT NOT NULL, created REAL NOT NULL, synced INTEGER DEFAULT 0, sync_error TEXT,
              PRIMARY KEY(device, receipt));
            CREATE TABLE IF NOT EXISTS pairing(id TEXT PRIMARY KEY, code_hash TEXT, expires REAL,
              attempts INTEGER DEFAULT 0, claimed TEXT, device TEXT);
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE,
              active INTEGER DEFAULT 1, seen REAL, created REAL);
            CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, device TEXT, kind TEXT,
              state TEXT, created REAL, expires REAL, received REAL, finished REAL, result TEXT);
            CREATE TABLE IF NOT EXISTS venue_settings(venue TEXT PRIMARY KEY,
              device_id TEXT NOT NULL DEFAULT '', tax TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS device_commands ON commands(device, created);
            ''')
            if 'payment_timing' not in {r[1] for r in db.execute('PRAGMA table_info(venue_settings)')}:
                db.execute("ALTER TABLE venue_settings ADD COLUMN payment_timing TEXT NOT NULL DEFAULT 'before'")
            for table in ('pairing', 'devices', 'commands', 'refunds'):
                if 'venue' not in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN venue TEXT NOT NULL DEFAULT 'demo.padacha.ru'")
                db.execute(f'CREATE INDEX IF NOT EXISTS {table}_venue ON {table}(venue)')
            for table in ('commands', 'refunds'):
                if 'last_sync_attempt' not in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN last_sync_attempt REAL NOT NULL DEFAULT 0')
            if 'code_lookup' not in {r[1] for r in db.execute('PRAGMA table_info(pairing)')}:
                db.execute('ALTER TABLE pairing ADD COLUMN code_lookup TEXT')
                db.execute('UPDATE pairing SET expires=0 WHERE claimed IS NULL')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS pairing_code_lookup ON pairing(code_lookup)')
            columns = {r[1] for r in db.execute('PRAGMA table_info(commands)')}
            for name, definition in [('payload', 'TEXT'), ('synced', 'INTEGER DEFAULT 0'), ('sync_error', 'TEXT')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE commands ADD COLUMN {name} {definition}')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def transact(self, action, body, *, venue=None):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            result = self.execute(db, action, body, venue)
            db.commit()
            return result
        except QueueError:
            # Failed code attempts must survive the error response.
            db.commit()
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def execute(self, db, action, b, venue=None):
        now = time.time()
        if action.startswith(('admin/', 'internal/')):
            if not venue or not db.execute('SELECT 1 FROM venues WHERE id=?', (venue,)).fetchone():
                raise QueueError(403, 'Заведение не подтверждено')
            db.execute('INSERT OR IGNORE INTO venue_settings(venue) VALUES(?)', (venue,))
        if action in ('admin/settings', 'admin/settings-save'):
            current = dict(db.execute("SELECT device_id,tax,revision,payment_timing FROM venue_settings WHERE venue=?", (venue,)).fetchone())
            if action == 'admin/settings':
                return current
            if type(b.get('revision')) is not int or b['revision'] != current['revision']:
                raise QueueError(409, 'Настройки изменены с другого планшета. Обновите страницу и проверьте выбор кассы.')
            timing = b.get('payment_timing', current['payment_timing'])
            if timing not in ('before', 'after'):
                raise QueueError(400, 'Выберите оплату до или после приготовления')
            did, tax = b.get('device_id'), b.get('tax')
            if did == '' and tax == '':
                pass  # Explicitly disable sending new orders; existing receipts keep their snapshot.
            else:
                did = identifier(did)
                if tax not in ('NO_VAT', 'VAT_0', 'VAT_10'):
                    raise QueueError(400, 'Выберите ставку НДС')
                if not db.execute('SELECT 1 FROM devices WHERE id=? AND active=1 AND venue=?', (did, venue)).fetchone():
                    raise QueueError(400, 'Выберите подключённую кассу')
            db.execute("UPDATE venue_settings SET device_id=?,tax=?,payment_timing=?,revision=revision+1 WHERE venue=?", (did, tax, timing, venue))
            return {'device_id': did, 'tax': tax, 'revision': current['revision'] + 1, 'payment_timing': timing}
        if action == 'internal/order-settings':
            current = self.execute(db, 'admin/settings', {}, venue)
            if not current['device_id'] or not db.execute('SELECT 1 FROM devices WHERE id=? AND active=1 AND venue=?', (current['device_id'], venue)).fetchone():
                raise QueueError(409, 'Подключите кассу в «Настройки → Оплата и касса → Эвотор».')
            return current
        if action == 'admin/pair':
            # Globally unique code selects one tenant; issuing a code never invalidates another venue.
            for _ in range(20):
                code = f'{secrets.randbelow(100000000):08}'
                if not db.execute('SELECT 1 FROM pairing WHERE code_lookup=?', (digest(code),)).fetchone():
                    break
            else:
                raise QueueError(503, 'Не удалось создать код. Повторите позже.')
            pid = str(uuid.uuid4())
            db.execute('UPDATE pairing SET expires=0 WHERE venue=? AND claimed IS NULL', (venue,))
            db.execute('INSERT INTO pairing(id,code_hash,code_lookup,expires,venue) VALUES(?,?,?,?,?)',
                       (pid, digest(pid + ':' + code), digest(code), now + 300, venue))
            return {'code': code, 'expires': now + 300}
        if action == 'pair':
            client = digest(str(b.get('_client', 'local')))
            limit = db.execute('SELECT * FROM pairing_limits WHERE client=?', (client,)).fetchone()
            if limit and limit['started'] > now - 300 and limit['attempts'] >= 5:
                raise QueueError(429, 'Слишком много попыток. Подождите пять минут.')
            secret, code = b.get('secret', ''), b.get('code', '')
            if not isinstance(secret, str) or len(secret) != 64 or any(c not in '0123456789abcdef' for c in secret):
                raise QueueError(400, 'Некорректный ключ установки')
            p = db.execute('SELECT * FROM pairing WHERE code_lookup=? AND expires>?', (digest(code), now)).fetchone() if isinstance(code, str) else None
            token_hash = digest(secret)
            if not p or p['attempts'] >= 5 or not hmac.compare_digest(p['code_hash'], digest(p['id'] + ':' + code)) or (p['claimed'] and not hmac.compare_digest(p['claimed'], token_hash)):
                if p: db.execute('UPDATE pairing SET attempts=attempts+1 WHERE id=?', (p['id'],))
                if not limit or limit['started'] <= now - 300:
                    db.execute('INSERT OR REPLACE INTO pairing_limits(client,started,attempts) VALUES(?,?,1)', (client, now))
                else:
                    db.execute('UPDATE pairing_limits SET attempts=attempts+1 WHERE client=?', (client,))
                raise QueueError(403, 'Код неверный, истёк или уже использован')
            registered = db.execute('SELECT * FROM venues WHERE id=?', (p['venue'],)).fetchone()
            if not registered:
                raise QueueError(403, 'Заведение не подтверждено')
            if p['device']:
                d = db.execute('SELECT * FROM devices WHERE id=? AND venue=? AND active=1', (p['device'], p['venue'])).fetchone()
                if not d:
                    raise QueueError(403, 'Касса отключена. Получите новый код')
                return {'device_id': d['id'], 'venue_id': p['venue'], 'venue_name': registered['name'], 'venue_host': registered['host']}
            if db.execute('SELECT 1 FROM devices WHERE token_hash=?', (token_hash,)).fetchone():
                raise QueueError(409, 'Эта установка уже привязана. Перенос кассы требует сверки.')
            did = str(uuid.uuid4())
            db.execute('INSERT INTO devices(id,token_hash,created,venue) VALUES(?,?,?,?)', (did, token_hash, now, p['venue']))
            db.execute('UPDATE pairing SET claimed=?,device=? WHERE id=?', (token_hash, did, p['id']))
            return {'device_id': did, 'venue_id': p['venue'], 'venue_name': registered['name'], 'venue_host': registered['host']}
        if action == 'admin/status':
            db.execute("UPDATE commands SET state='expired' WHERE state='queued' AND expires<? AND venue=?", (now, venue))
            devices = [dict(r) for r in db.execute('SELECT id,active,seen,created FROM devices WHERE venue=? ORDER BY created DESC', (venue,))]
            commands = [dict(r) for r in db.execute('SELECT * FROM commands WHERE venue=? ORDER BY created DESC LIMIT 50', (venue,))]
            for c in commands:
                c['result'] = json.loads(c['result']) if c['result'] else None
            connected = db.execute('SELECT 1 FROM venue_settings s JOIN devices d ON d.id=s.device_id AND d.venue=s.venue WHERE s.venue=?', (venue,)).fetchone() is not None
            return {'connected': connected, 'devices': devices, 'commands': commands, 'refunds': [dict(r) for r in db.execute('SELECT receipt,result,synced,sync_error FROM refunds WHERE venue=? ORDER BY created DESC LIMIT 100', (venue,))]}
        if action == 'admin/revoke':
            did = identifier(b.get('device_id'))
            db.execute('UPDATE devices SET active=0 WHERE id=? AND venue=?', (did, venue))
            db.execute("UPDATE commands SET state='cancelled' WHERE device=? AND venue=? AND state='queued'", (did, venue))
            # Received commands remain uncertain and are not replayed on a replacement device.
            return {'revoked': True}
        if action in ('admin/command', 'internal/order'):
            did, cid, kind = identifier(b.get('device_id')), identifier(b.get('id')), b.get('kind')
            if kind not in (('order_receipt',) if action == 'internal/order' else ('echo', 'open_receipt')):
                raise QueueError(400, 'Поддерживаются только тест обмена и тестовый чек')
            if not db.execute('SELECT 1 FROM devices WHERE id=? AND active=1 AND venue=?', (did, venue)).fetchone():
                raise QueueError(404, 'Касса не подключена')
            payload = json.dumps(b.get('payload'), sort_keys=True) if kind == 'order_receipt' else None
            if kind == 'order_receipt' and (not isinstance(b.get('payload'), dict) or b['payload'].get('device_id') != did):
                raise QueueError(400, 'Снимок заказа не соответствует кассе')
            old = db.execute('SELECT * FROM commands WHERE id=?', (cid,)).fetchone()
            if old:
                if old['venue'] != venue or old['device'] != did or old['kind'] != kind or old['payload'] != payload:
                    raise QueueError(409, 'ID занят другой командой')
                return {'id': cid, 'state': old['state']}
            db.execute("UPDATE commands SET state='expired' WHERE state='queued' AND expires<? AND venue=?", (now, venue))
            if db.execute("SELECT 1 FROM commands WHERE device=? AND state IN ('queued','received')", (did,)).fetchone():
                raise QueueError(409, 'Дождитесь предыдущей команды')
            db.execute("INSERT INTO commands(id,device,kind,state,created,expires,venue) VALUES(?,?,?,'queued',?,?,?)", (cid, did, kind, now, now + 60, venue))
            db.execute('UPDATE commands SET payload=? WHERE id=?', (payload, cid))
            return {'id': cid, 'state': 'queued'}
        if action not in ('next', 'result', 'refund'):
            raise QueueError(404, 'Маршрут не найден')
        token = b.get('token', '')
        if not isinstance(token, str) or len(token) != 64:
            raise QueueError(401, 'Требуется ключ кассы')
        d = db.execute('SELECT * FROM devices WHERE token_hash=? AND active=1', (digest(token),)).fetchone()
        if not d:
            raise QueueError(401, 'Ключ кассы отозван или недействителен')
        db.execute('UPDATE devices SET seen=? WHERE id=?', (now, d['id']))
        if action == 'refund':
            r = b.get('result')
            if not isinstance(r, dict) or r.get('status') != 'receipt_refunded':
                raise QueueError(400, 'Нет подтверждения завершённого возврата')
            receipt = identifier(r.get('receipt_uuid'))
            # Preserve evidence verbatim; business validation happens in the demo bridge.
            clean = {k: r.get(k) for k in ('status', 'receipt_uuid', 'base_receipt_uuid', 'amount',
                'method', 'receipt_number', 'session_number', 'completed_at', 'positions', 'discount')}
            clean['device_id'] = d['id']
            encoded = json.dumps(clean, sort_keys=True)
            if len(encoded) > 60000:
                raise QueueError(400, 'Слишком большой чек возврата')
            old = db.execute('SELECT result FROM refunds WHERE device=? AND receipt=?', (d['id'], receipt)).fetchone()
            if old and old['result'] != encoded:
                raise QueueError(409, 'Другой возврат с этим UUID уже сохранён. Нужна сверка.')
            db.execute('INSERT OR IGNORE INTO refunds(device,receipt,result,created,venue) VALUES(?,?,?,?,?)', (d['id'], receipt, encoded, now, d['venue']))
            return {'ack': receipt}
        if action == 'next':
            db.execute("UPDATE commands SET state='expired' WHERE device=? AND state='queued' AND expires<?", (d['id'], now))
            c = db.execute("SELECT * FROM commands WHERE device=? AND state IN ('queued','received') ORDER BY created LIMIT 1", (d['id'],)).fetchone()
            if not c:
                return {'command': None}
            db.execute("UPDATE commands SET state='received',received=COALESCE(received,?) WHERE id=?", (now, c['id']))
            return {'command': {'id': c['id'], 'kind': c['kind'], 'test_only': c['kind'] != 'order_receipt', 'payload': json.loads(c['payload']) if c['payload'] else None, 'remaining_seconds': max(0, int(c['expires'] - now))}}
        cid = identifier(b.get('id'))
        c = db.execute('SELECT * FROM commands WHERE id=? AND device=?', (cid, d['id'])).fetchone()
        if not c:
            raise QueueError(404, 'Команда не найдена')
        r = b.get('result')
        allowed = ('echo_ok', 'error', 'needs_review') if c['kind'] == 'echo' else ('receipt_opened', 'error', 'needs_review')
        if c['kind'] == 'order_receipt':
            allowed = ('receipt_opened', 'receipt_paid', 'receipt_cleared', 'error', 'needs_review')
        if not isinstance(r, dict) or r.get('status') not in allowed:
            raise QueueError(400, 'Результат не соответствует команде')
        clean = {k: str(r.get(k, ''))[:500] for k in ('status', 'message', 'receipt_uuid')}
        if clean['status'] in ('receipt_opened', 'receipt_paid', 'receipt_cleared'):
            clean['receipt_uuid'] = identifier(clean['receipt_uuid'])
        if clean['status'] == 'receipt_cleared':
            if r.get('clear_evidence') not in ('deleted_event_and_absent', 'empty_unpaid_draft', 'owner_verified_deleted'):
                raise QueueError(400, 'Нет подтверждения удаления неоплаченного чека')
            clean['clear_evidence'] = r['clear_evidence']
        if clean['status'] == 'receipt_paid':
            clean.update({k: r.get(k) for k in ('amount', 'method', 'device_id', 'order_id', 'session_number', 'receipt_number')})
            if clean['device_id'] != d['id']:
                raise QueueError(400, 'Другая касса в подтверждении')
        encoded = json.dumps(clean, sort_keys=True)
        transition = False
        if c['result'] and c['kind'] == 'order_receipt':
            previous = json.loads(c['result'])
            transition = previous['status'] == 'receipt_opened' and clean['status'] in ('receipt_paid', 'needs_review', 'receipt_cleared') and previous['receipt_uuid'] == clean['receipt_uuid']
        if c['result'] and not transition:
            if c['result'] != encoded:
                raise QueueError(409, 'Другой результат уже сохранён')
        elif c['state'] != 'received' and not transition:
            raise QueueError(409, 'Команда не была выдана кассе')
        else:
            db.execute('UPDATE commands SET state=?,result=?,finished=?,synced=0,sync_error=NULL WHERE id=?', (clean['status'], encoded, now, cid))
        return {'ack': cid}

    def pending_results(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM commands WHERE kind='order_receipt' AND result IS NOT NULL AND synced=0 ORDER BY last_sync_attempt,created LIMIT 100")]

    def mark_synced(self, cid, result, error=None):
        with self.connect() as db:
            db.execute('UPDATE commands SET synced=?,sync_error=?,last_sync_attempt=? WHERE id=? AND result=?',
                       (0 if error else 1, error, time.time(), cid, result))

    def command_status(self, cid, *, venue):
        with self.connect() as db:
            row = db.execute('SELECT state,result,synced,sync_error FROM commands WHERE id=? AND venue=?', (cid, venue)).fetchone()
            return dict(row) if row else None

    def pending_refunds(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM refunds WHERE synced=0 ORDER BY last_sync_attempt,created LIMIT 100')]

    def mark_refund_synced(self, row, error=None):
        with self.connect() as db:
            db.execute('UPDATE refunds SET synced=?,sync_error=?,last_sync_attempt=? WHERE device=? AND receipt=? AND result=?',
                (0 if error else 1, error, time.time(), row['device'], row['receipt'], row['result']))

    def register_venue(self, identity):
        """Only the authenticated bridge can supply an identity; never browser JSON."""
        venue, host, name = str(identity['venue_id']), identity['venue_host'], identity['venue_name']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            other = db.execute('SELECT id FROM venues WHERE host=?', (host,)).fetchone()
            if other and other['id'] != venue:
                raise QueueError(409, 'Домен уже связан с другим заведением. Нужна сверка.')
            db.execute('INSERT INTO venues(id,host,name) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET host=excluded.host,name=excluded.name', (venue, host, name))
            if host == 'demo.padacha.ru' and venue != host:
                # One-time migration of the explicitly demo-only historical database.
                legacy = db.execute("SELECT 1 FROM venue_settings WHERE venue='demo.padacha.ru'").fetchone()
                if legacy:
                    if db.execute('SELECT 1 FROM venue_settings WHERE venue=?', (venue,)).fetchone():
                        raise QueueError(409, 'Настройки demo требуют сверки перед переносом.')
                    db.execute("UPDATE venue_settings SET venue=? WHERE venue='demo.padacha.ru'", (venue,))
                    for table in ('devices','commands','pairing','refunds'):
                        db.execute(f"UPDATE {table} SET venue=? WHERE venue='demo.padacha.ru'", (venue,))
            db.execute('INSERT OR IGNORE INTO venue_settings(venue) VALUES(?)', (venue,))
        return venue

    def venue_identity(self, venue):
        with self.connect() as db:
            row = db.execute('SELECT * FROM venues WHERE id=?', (venue,)).fetchone()
            if not row:
                raise QueueError(409, 'Заведение ещё не подтверждено. Откройте настройки подключения.')
            return dict(row)
