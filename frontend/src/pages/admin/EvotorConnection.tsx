import { useEffect, useState } from 'react';
import { post } from '../../api';
import Icon from '../../components/Icon';
import type { CloudSettings } from '../counter/EvotorCloud';

type Device = { id: string; active: number; seen: number | null };
export default function EvotorConnection({ settings, onSaved }: { settings: CloudSettings; onSaved: (next: CloudSettings) => void }) {
  const [link, setLink] = useState(settings);
  const [devices, setDevices] = useState<Device[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [connectionError, setConnectionError] = useState('');
  const [pairing, setPairing] = useState<{ code: string; expires: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    let disposed = false;
    let timer: number;
    async function poll() {
      try {
        const result = await post<{ devices: Device[] }>('/evotor-cloud/admin/status/', {});
        if (!disposed) { setDevices(result.devices.filter(d => d.active)); setLoaded(true); setConnectionError(''); }
      } catch (e) {
        if (!disposed) setConnectionError(e instanceof Error ? e.message : 'Не удалось проверить подключение');
      } finally { if (!disposed) timer = window.setTimeout(poll, 3000); }
    }
    void poll();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, []);
  async function pair() {
    setBusy(true); setMessage('');
    try { setPairing(await post('/evotor-cloud/admin/pair/', {})); }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Не удалось получить код'); }
    finally { setBusy(false); }
  }
  async function save() {
    setBusy(true); setMessage('');
    try {
      const next = await post<CloudSettings>('/evotor-cloud/admin/settings-save/', { device_id: link.device_id, tax: link.tax, revision: link.revision });
      setLink(next); onSaved(next);
      setMessage('Касса и НДС сохранены для заведения. Настройки доступны на всех планшетах.');
    } catch (e) { setMessage(e instanceof Error ? e.message : 'Не удалось сохранить настройки заведения'); }
    finally { setBusy(false); }
  }
  const selected = devices.find(d => d.id === link.device_id);
  const online = selected?.seen && Date.now() / 1000 - selected.seen < 15;
  const codeActive = pairing && pairing.expires * 1000 > Date.now();
  return <>
    <div className="rule-top mt-3 pt-3">
      <strong className="title">Подключение Эвотора</strong>
      {settings.venue_name && <p className="sm">Заведение: {settings.venue_name} · {settings.venue_host}</p>}
      <p className="muted sm mt-2">Если касса уже подключена, выберите её ниже. Для новой кассы получите код и введите его в приложении «Падача» на кассе: «Подключение кассы» (версия 0.14 или новее).</p>
      {connectionError && <p className="error sm" role="alert">{connectionError}</p>}
      {!loaded && !connectionError && <p className="muted sm" role="status">Проверяю подключённые кассы…</p>}
      {loaded && !devices.length && <p className="muted sm">Подключённых касс пока нет.</p>}
      <button type="button" className="btn sm ghost" disabled={busy} onClick={pair}>
        <Icon name="plus" size={15} /> {busy ? 'Получаю код…' : devices.length ? 'Подключить ещё одну кассу' : 'Получить код подключения'}
      </button>
      {pairing && <div className="mt-3" role="status">
        {codeActive ? <><span className="label">Код для кассы</span><strong className="h2" style={{ display: 'block', letterSpacing: '0.15em', fontVariantNumeric: 'tabular-nums' }}>{pairing.code}</strong>
          <p className="muted sm">Введите до {new Date(pairing.expires * 1000).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })}. Подключённая касса появится в списке ниже.</p></>
          : <p className="muted sm">Срок кода истёк. Для нового подключения получите код ещё раз.</p>}
      </div>}
      <label className="field mt-3"><span className="label">Подключённая касса</span>
        <select className="input" value={link.device_id || ''} onChange={e => { setLink({ ...link, device_id: e.target.value }); setMessage(''); }}>
          <option value="">Выберите кассу</option>
          {link.device_id && !selected && <option value={link.device_id} disabled>Сохранённая касса недоступна</option>}
          {devices.map(d => <option key={d.id} value={d.id}>Эвотор · {d.id.slice(0, 8)}</option>)}
        </select>
      </label>
      {selected && <p className="muted sm" role="status">{online ? 'Касса на связи' : 'Касса не на связи. Проверьте интернет и откройте «Падачу» на кассе.'}</p>}
      <label className="field"><span className="label">НДС для всех позиций</span>
        <select className="input" value={link.tax || ''} onChange={e => { setLink({ ...link, tax: e.target.value }); setMessage(''); }}>
          <option value="">Выберите ставку</option><option value="NO_VAT">Без НДС</option><option value="VAT_0">НДС 0%</option><option value="VAT_10">НДС 10%</option>
        </select>
      </label>
      <button type="button" className="btn sm mt-3" disabled={busy || !selected || !link.tax || !!connectionError} onClick={save}><Icon name="check" size={15} /> Сохранить</button>
      {message && <p className="sm" role="status">{message}</p>}
      <p className="muted sm mt-3 m-0">Касса и НДС общие для заведения. На других планшетах повторное подключение не требуется. У заказа на стойке нажмите «Оплатить». Оплату выбирают на кассе; открытие чека не отмечает заказ оплаченным.</p>
    </div>
  </>;
}
