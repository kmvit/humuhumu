import { get, post } from './api';
export type LanLink = { address: string; token: string; tax: string; device_id: string };
export type LanResult = { status: string; message: string; receipt_uuid: string; retryable?: boolean };
type Transfer = { id: string; payload: unknown; result: Partial<LanResult> };
const KEY = 'padacha.evotor.lan.v1';
export function loadLink(): LanLink | null {
  try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { return null; }
}
export function forgetLink() { localStorage.removeItem(KEY); }
function addressOf(value: string) {
  const url = new URL(value.includes('://') ? value : `http://${value}${value.includes(':') ? '' : ':8765'}`);
  const p = url.hostname.split('.').map(Number);
  if (url.protocol !== 'http:' || url.port !== '8765' || url.username || url.password || url.search || url.hash || url.pathname !== '/' ||
    p.length !== 4 || !p.every(n => Number.isInteger(n) && n >= 0 && n <= 255) ||
    !(p[0] === 10 || p[0] === 192 && p[1] === 168 || p[0] === 172 && p[1] >= 16 && p[1] <= 31)) {
    throw new Error('Введите адрес с экрана кассы: http://192.168.…:8765');
  }
  return url.origin;
}
export class LanHttpError extends Error { constructor(public status: number, message: string) { super(message); } }
async function local<T>(address: string, path: string, token: string, body?: unknown): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 10000);
  try {
    const res = await fetch(addressOf(address) + path, {
      method: body === undefined ? 'GET' : 'POST', mode: 'cors', credentials: 'omit', cache: 'no-store', redirect: 'error',
      headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json; charset=UTF-8' }), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal,
    });
    const data = await res.json();
    if (!res.ok) throw new LanHttpError(res.status, data.detail || `Касса ответила HTTP ${res.status}`);
    return data as T;
  } catch (e) {
    if (e instanceof TypeError || e instanceof DOMException) throw new Error('Нет связи с кассой. Проверьте Wi-Fi, адрес и разрешение Chrome на локальную сеть. Повторная проверка использует ту же команду.');
    throw e;
  } finally { window.clearTimeout(timer); }
}
export async function pairLan(address: string, code: string, tax: string) {
  const normalized = addressOf(address);
  if (!/^\d{6}$/.test(code)) throw new Error('Введите 6 цифр с кассы');
  if (!tax) throw new Error('Укажите ставку НДС для пробных чеков');
  const retryKey = `${KEY}.pair`;
  let retry: { address: string; code: string; nonce: string } | null = null;
  try { retry = JSON.parse(sessionStorage.getItem(retryKey) || 'null'); } catch { /* fresh pairing */ }
  if (retry?.address !== normalized || retry?.code !== code) {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    retry = { address: normalized, code, nonce: Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('') };
    sessionStorage.setItem(retryKey, JSON.stringify(retry));
  }
  const paired = await local<{ token: string; device_id: string }>(normalized, '/v1/pair', '', { code, nonce: retry.nonce });
  const link = { address: normalized, token: paired.token, tax, device_id: paired.device_id };
  localStorage.setItem(KEY, JSON.stringify(link)); sessionStorage.removeItem(retryKey);
  return link;
}
export async function pingLan(link: LanLink) { await local(link.address, '/v1/ping', link.token); }
export async function sendLanOrder(orderId: number, link: LanLink): Promise<LanResult> {
  const endpoint = `/orders/${orderId}/evotor-lan/`;
  const capabilities = await local<{ payment_tracking?: boolean }>(link.address, '/v1/ping', link.token);
  if (!capabilities.payment_tracking) throw new Error('Обновите приложение Падача на кассе до версии 0.6 для возврата оплаты.');
  const transfer = await post<Transfer>(endpoint, { tax: link.tax, device_id: link.device_id });
  if (transfer.result.status && !transfer.result.retryable) return transfer.result as LanResult;
  let result = await local<LanResult>(link.address, '/v1/commands', link.token, { id: transfer.id, payload: JSON.stringify(transfer.payload) });
  const deadline = Date.now() + 30000;
  while (result.status === 'accepted' || result.status === 'started') {
    if (Date.now() >= deadline) throw new Error('Касса ещё не вернула результат. Проверьте чек и нажмите кнопку снова — второй чек не создастся.');
    await new Promise(resolve => window.setTimeout(resolve, 500));
    result = await local<LanResult>(link.address, `/v1/commands/${transfer.id}`, link.token);
  }
  await post(endpoint, { id: transfer.id, result });
  return result;
}


export async function syncLanPayments(link: LanLink, onPaid: () => void): Promise<string> {
  const pending = await get<{ id: string; order_id: number; result: Partial<LanResult> }[]>(
    `/orders/evotor-lan-pending/?device_id=${encodeURIComponent(link.device_id)}`);
  let message = pending.length ? 'Ожидаю оплату на кассе' : 'Подтверждения оплат синхронизированы';
  for (const transfer of pending) {
    try {
      const result = await local<LanResult>(link.address, `/v1/commands/${transfer.id}`, link.token);
      if (['accepted', 'started'].includes(result.status)) continue;
      if (result.status === 'receipt_opened' && transfer.result.status === 'receipt_opened') continue;
      await post(`/orders/${transfer.order_id}/evotor-lan/`, { id: transfer.id, result });
      if (result.status === 'receipt_paid') { onPaid(); message = `Заказ №${transfer.order_id} оплачен — подтверждение сохранено`; }
      else if (result.status === 'needs_review' || result.status === 'error') message = `Заказ №${transfer.order_id}: ${result.message}`;
    } catch (e) {
      if (e instanceof LanHttpError && e.status === 404) continue;
      message = `Заказ №${transfer.order_id}: ${e instanceof Error ? e.message : 'не удалось получить подтверждение'}`;
    }
  }
  return message;
}
