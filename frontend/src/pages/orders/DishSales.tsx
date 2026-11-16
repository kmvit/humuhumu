import { useEffect, useState } from "react";
import { ApiError, get } from "../../api";
import "./DishSales.css";

type Totals = { quantity: number; amount: string; returned_quantity: number; returned_amount: string };
type Row = Totals & { id: number; name: string; category: string };
type Report = { rows: Row[]; totals: Totals };
const money = (n: string) => Number(n).toLocaleString("ru", { maximumFractionDigits: 2 }) + " ₽";
function day(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function DishSales() {
  const today = day(new Date());
  const [start, setStart] = useState(today);
  const [end, setEnd] = useState(today);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("quantity");
  const [data, setData] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const valid = Boolean(start && end && start <= end);
  useEffect(() => {
    let alive = true;
    setData(null);
    setError("");
    if (valid) get<Report>(`/orders/sales/?start=${start}&end=${end}`)
      .then(r => { if (alive) setData(r); })
      .catch(e => { if (alive) setError(e instanceof ApiError ? e.message : "Не удалось загрузить продажи"); });
    return () => { alive = false; };
  }, [start, end, valid, retry]);
  function preset(days: number) {
    const d = new Date();
    if (days === 0) d.setDate(1);
    else d.setDate(d.getDate() - days + 1);
    setStart(day(d)); setEnd(today);
  }
  const rows = (data?.rows ?? []).filter(r =>
    r.name.toLocaleLowerCase("ru").includes(query.toLocaleLowerCase("ru")) && (!category || r.category === category)
  ).sort((a, b) => sort === "name" ? a.name.localeCompare(b.name, "ru") :
    Number(b[sort as "quantity" | "amount"]) - Number(a[sort as "quantity" | "amount"]));
  return <section>
    <div className="sales-period mt-3">
      <div className="sales-presets">
      <button className="btn sm ghost" onClick={() => preset(1)}>Сегодня</button>
      <button className="btn sm ghost" onClick={() => preset(7)}>7 дней</button>
      <button className="btn sm ghost" onClick={() => preset(0)}>Этот месяц</button>
      </div>
      <div className="sales-dates">
      <label><span>С</span><input className="input" type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
      <label><span>По</span><input className="input" type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
      </div>
    </div>
    <p className="muted sm mt-3">Продажи — по дате оплаты, возвраты — по дате возврата. Суммы включают добавки, до списания бонусов. Отменённые заказы исключены.</p>
    {!valid && <p role="alert">Укажите даты: начало периода не позже окончания.</p>}
    {error && <p role="alert">{error} <button className="btn sm ghost" onClick={() => setRetry(v => v + 1)}>Повторить</button></p>}
    {valid && !data && !error && <p role="status">Загрузка продаж…</p>}
    {data && <>
      <div className="grid stats mt-3">
        {[['Продано за период', `${data.totals.quantity} шт.`], ['Сумма продаж', money(data.totals.amount)], ['Возвраты за период', `${data.totals.returned_quantity} шт. · ${money(data.totals.returned_amount)}`]].map(([label, value]) =>
          <div className="card" key={label}><div className="muted">{label}</div><div className="stat-value">{value}</div></div>)}
      </div>
      <div className="sales-filters mt-3">
        <input className="input" aria-label="Поиск блюда" placeholder="Найти блюдо или напиток" value={query} onChange={e => setQuery(e.target.value)} />
        <select className="input" aria-label="Категория" value={category} onChange={e => setCategory(e.target.value)}><option value="">Все категории</option>{Array.from(new Set(data.rows.map(r => r.category))).sort().map(c => <option key={c}>{c}</option>)}</select>
        <select className="input" aria-label="Сортировка" value={sort} onChange={e => setSort(e.target.value)}><option value="quantity">По количеству</option><option value="amount">По сумме</option><option value="name">По названию</option></select>
      </div>
      <div className="card mt-3" style={{ overflowX: "auto" }}>
        {rows.length ? <table style={{ width: "100%", textAlign: "left", borderSpacing: "0 16px" }}>
          <thead><tr><th>Блюдо / напиток</th><th>Продано, шт.</th><th>Сумма</th><th>Возврат, шт.</th><th>Сумма возврата</th></tr></thead>
          <tbody>{rows.map(r => <tr key={r.id}><td><strong>{r.name}</strong><div className="muted sm">{r.category}</div></td><td>{r.quantity}</td><td style={{ whiteSpace: "nowrap" }}>{money(r.amount)}</td><td>{r.returned_quantity || '—'}</td><td style={{ whiteSpace: "nowrap" }}>{Number(r.returned_amount) ? money(r.returned_amount) : '—'}</td></tr>)}</tbody>
        </table> : <p className="muted">{data.rows.length ? "По заданным фильтрам ничего не найдено." : "За этот период продаж и возвратов нет."}</p>}
      </div>
    </>}
  </section>;
}
