import { useEffect, useRef, useState } from 'react';
import { post } from '../../api';
export type CloudSettings = { device_id: string; tax: string; revision: number; payment_timing: 'before' | 'after'; venue_id?: string; venue_name?: string; venue_host?: string };
type Command = { id: string; state: string; payload: string | null; result: { status: string; message: string } | null; synced: number; sync_error: string | null };
type Refund = { receipt: string; result: string; synced: number; sync_error: string | null };
type Status = { connected?: boolean; refunds?: Refund[]; devices: { id: string; active: number; seen: number | null }[]; commands: Command[] };
export function useCloudOrders(enabled: boolean, onPaid: () => void) {
  const [status, setStatus] = useState<Status>({ devices: [], commands: [] });
  const callback = useRef(onPaid); callback.current = onPaid;
  const paid = useRef(new Set<string>());
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let timer: number;
    async function tick() {
      try {
        const next = await post<Status>('/evotor-cloud/admin/status/', {});
        if (!disposed) {
          setStatus(next);
          for (const r of next.refunds || []) if (r.synced && !paid.current.has(`refund:${r.receipt}`)) {
            paid.current.add(`refund:${r.receipt}`); callback.current();
          }
          for (const c of next.commands) if (c.state === 'receipt_paid' && c.synced && !paid.current.has(c.id)) {
            paid.current.add(c.id); callback.current();
          }
        }
      } catch { /* Sending reports connection and authentication errors. */ }
      finally { if (!disposed) timer = window.setTimeout(tick, 3000); }
    }
    void tick();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [enabled]);
  return status;
}
export function EvotorCloudOrder({ orderId, status }: { orderId: number; status: Status }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const command = status.commands.find(c => {
    try { return c.payload && JSON.parse(c.payload).order_id === orderId; } catch { return false; }
  });
  async function send() {
    setBusy(true);
    try {
      await post('/evotor-cloud/admin/order/', { order_id: orderId, ...(command?.state === 'receipt_cleared' ? { retry_of: command.id } : {}) });
      setMessage('Заказ в очереди. Ожидаю кассу.');
    } catch (e) { setMessage(e instanceof Error ? e.message : 'Не удалось передать заказ'); }
    finally { setBusy(false); }
  }
  const progress = command?.sync_error ? `Нужна сверка: ${command.sync_error}` : (command?.result?.status === 'receipt_opened' ? 'Ожидает оплаты на кассе' : command?.result?.status === 'receipt_cleared' ? 'Чек удалён на кассе. Нажмите «Оплатить заново».' : command?.result?.message) || ({ queued: 'Заказ в очереди', received: 'Касса получила заказ', expired: 'Касса не получила заказ за минуту. Требуется проверка передачи.', cancelled: 'Передача отменена' } as Record<string, string>)[command?.state || ''];
  return <div className="mt-2"><button className="btn sm block ghost" disabled={busy} onClick={send}>{busy ? 'Подготовка…' : command?.state === 'receipt_cleared' ? 'Оплатить заново' : 'Оплатить'}</button>{(message || progress) && <p className="sm" role="status">{busy ? message : progress || message}</p>}</div>;
}

export function EvotorRefunds({ status }: { status: Status }) {
  const pending = (status.refunds || []).filter(r => !r.synced);
  if (!pending.length) return null;
  return <div className="card" role="status"><strong>Возвраты с кассы</strong>{pending.map(r => <p key={r.receipt} className="sm">{r.sync_error ? `Нужна сверка: ${r.sync_error}` : 'Возврат получен. Обновляем заказ и склад.'} · чек {r.receipt.slice(0, 8)}</p>)}</div>;
}
