import { useEffect, useState } from 'react';
import { forgetLink, loadLink, pairLan, pingLan, sendLanOrder, syncLanPayments } from '../../evotorLan';
export function EvotorLanSettings() {
  const [link, setLink] = useState(loadLink);
  const [address, setAddress] = useState(() => loadLink()?.address || '');
  const [code, setCode] = useState('');
  const [tax, setTax] = useState(() => loadLink()?.tax || '');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function connect() {
    setBusy(true); setMessage('Подключаю…');
    try { setLink(await pairLan(address.trim(), code.trim(), tax)); setCode(''); setMessage('Планшет подключён к кассе'); }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Не удалось подключиться'); }
    finally { setBusy(false); }
  }
  async function check() {
    if (!link) return;
    setBusy(true);
    try { await pingLan(link); setMessage('Касса ответила по Wi-Fi'); }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Нет связи'); }
    finally { setBusy(false); }
  }
  return <details className="card mt-3"><summary>Эвотор по Wi-Fi · проверка чеков</summary>
    <p>Откройте «Падачу» на кассе. Планшет и касса должны быть в одной сети. Используйте актуальный Chrome и разрешите доступ к локальной сети.</p>
    <p className="muted">Передаём позиции в чек. После оплаты на кассе открытый экран стойки автоматически получит подтверждение. Если планшет был закрыт или без сети, подтверждение придёт при возвращении. Бонусное списание и смешанная оплата пока не поддерживаются.</p>
    {link && <p>Сохранена касса: {link.address}</p>}
    <label className="field"><span className="label">Адрес с экрана кассы</span><input className="input" value={address} onChange={e => setAddress(e.target.value)} placeholder="http://192.168.1.38:8765" /></label>
    <label className="field"><span className="label">Код с экрана кассы</span><input className="input" inputMode="numeric" maxLength={6} value={code} onChange={e => setCode(e.target.value.replace(/\D/g, ''))} autoComplete="off" /></label>
    <label className="field"><span className="label">НДС для всех позиций пробного чека</span><select className="input" value={tax} onChange={e => setTax(e.target.value)}><option value="">Выберите ставку</option><option value="NO_VAT">Без НДС</option><option value="VAT_0">НДС 0%</option><option value="VAT_10">НДС 10%</option></select></label>
    <div className="inline mt-2"><button className="btn" disabled={busy || !code || !tax} onClick={connect}>Подключить по коду</button>
      {link && <><button className="btn ghost" disabled={busy} onClick={check}>Проверить связь</button><button className="btn ghost" disabled={busy} onClick={() => { forgetLink(); setLink(null); setMessage('Привязка удалена с планшета'); }}>Забыть кассу</button></>}
    </div><p role="status">{message}</p>
  </details>;
}
export function EvotorLanOrder({ orderId }: { orderId: number }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function send() {
    const link = loadLink();
    if (!link) { setMessage('Сначала подключите кассу в разделе «Эвотор по Wi-Fi» над заказами.'); return; }
    setBusy(true); setMessage('Передаю заказ…');
    try { const result = await sendLanOrder(orderId, link); setMessage(result.message); }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Не удалось передать заказ'); }
    finally { setBusy(false); }
  }
  return <div className="mt-2"><button className="btn sm block ghost" disabled={busy} onClick={send}>{busy ? 'Проверяю чек…' : 'Чек на Эвотор'}</button>{message && <p className="sm" role="status">{message}</p>}</div>;
}


/** One poller for the whole counter, including while composing the next order. */
export function useEvotorPayments(enabled: boolean, onPaid: () => void) {
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let running = false;
    let timer: number | undefined;
    async function tick() {
      if (disposed || running) return;
      running = true;
      try {
        const link = loadLink();
        if (link && document.visibilityState !== 'hidden') {
          const next = await syncLanPayments(link, () => { if (!disposed) onPaid(); });
          if (!disposed) setMessage(next);
        }
      } catch (e) {
        if (!disposed) setMessage(e instanceof Error ? e.message : 'Нет связи для подтверждения оплат');
      } finally {
        running = false;
        if (!disposed) timer = window.setTimeout(tick, 2000);
      }
    }
    function wake() { window.clearTimeout(timer); void tick(); }
    void tick();
    window.addEventListener('online', wake);
    document.addEventListener('visibilitychange', wake);
    return () => { disposed = true; window.clearTimeout(timer); window.removeEventListener('online', wake); document.removeEventListener('visibilitychange', wake); };
  }, [enabled, onPaid]);
  return message;
}
