import { useEffect, useState } from "react";
import { get, ApiError } from "../../api";
import Icon from "../../components/Icon";

/** Раздел «Заказы» у владельца: заказы и платежи за день.

    Раньше на главной админки был список «последних заказов» — номер и
    сумма, без того, как и чем заплатили. Сверять выручку по нему было
    нельзя: карта онлайн и карта на кассе — разные деньги на разных счетах,
    а наличные мимо кассы не видны вовсе.

    Данные — из /api/payments/journal/: один запрос на день, итоги считает
    сервер, чтобы цифры здесь не разошлись с журналом платежей.
*/
type Channel = "" | "manual" | "online" | "kassa";

type JournalOrder = {
  id: number;
  number: number | null;
  created_at: string;
  customer_name: string;
  table: string;
  items: string[];
  total: string;
  payable: string;
  bonus_spent: string;
  status: string;
  status_display: string;
  paid: boolean;
  channel: Channel;
  channel_display: string;
  method: string;
  method_display: string;
  fiscal_receipt: string;
};

type JournalPayment = {
  id: number;
  created_at: string;
  order: number | null;
  order_number: number | null;
  customer_name: string;
  amount: string;
  refund: boolean;
  status: string;
  status_display: string;
  method: string;
  method_display: string;
  channel: Channel;
  channel_display: string;
  provider_display: string;
  fiscal_receipt: string;
};

type Journal = {
  date: string;
  orders: JournalOrder[];
  rows: JournalPayment[];
  totals: {
    cash: string;
    card_kassa: string;
    online: string;
    refunds: string;
    income: string;
    net: string;
  };
};

const money = (v: string | number) =>
  Math.round(Number(v)).toLocaleString("ru") + " ₽";

const time = (iso: string) =>
  new Date(iso).toLocaleTimeString("ru", { hour: "2-digit", minute: "2-digit" });

/** Дата в формате для input[type=date] — по местному времени, не UTC. */
function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function shiftDay(day: string, by: number): string {
  const d = new Date(day + "T12:00:00");
  d.setDate(d.getDate() + by);
  return isoDay(d);
}

/** Плашка статуса заказа: у «ждёт оплаты», «к оплате» и «возврат» своих
 *  стилей нет — берём ближайшие по смыслу. */
const ORDER_BADGE: Record<string, string> = {
  paid: "paid",
  open: "open",
  awaiting: "pending",
  unpaid: "pending",
  requested: "pending",
  refunded: "cancelled",
  cancelled: "cancelled",
};

/** «Касса · Картой», «Онлайн», «Отметка сотрудника · Наличные». */
function howPaid(channel: Channel, method: string, methodDisplay: string): string {
  if (!channel) return "";
  if (channel === "online") return "Онлайн";
  const m = method === "cash" ? "наличными" : method ? "картой" : "";
  const where = channel === "kassa" ? "На кассе" : "Отметка сотрудника";
  return m ? `${where} · ${m}` : methodDisplay ? `${where} · ${methodDisplay}` : where;
}

export default function OrdersJournal() {
  const today = isoDay(new Date());
  const [day, setDay] = useState(today);
  const [tab, setTab] = useState<"orders" | "payments">("orders");
  const [data, setData] = useState<Journal | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setError("");
    get<Journal>(`/payments/journal/?date=${day}`)
      .then((j) => alive && setData(j))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "Не удалось загрузить"));
    return () => {
      alive = false;
    };
  }, [day]);

  const t = data?.totals;
  const tiles = t
    ? [
        { icon: "cash", label: "Наличными", value: money(t.cash) },
        { icon: "card", label: "Картой на кассе", value: money(t.card_kassa) },
        { icon: "laptop", label: "Онлайн", value: money(t.online) },
        ...(Number(t.refunds) ? [{ icon: "minus", label: "Возвраты", value: "−" + money(t.refunds) }] : []),
        { icon: "chart", label: "Итого за день", value: money(t.net) },
      ]
    : [];

  return (
    <>
      <h1 className="h1">Заказы и платежи</h1>

      <div className="wrap mt-3" style={{ alignItems: "center" }}>
        <button className="btn sm ghost" onClick={() => setDay(shiftDay(day, -1))} aria-label="Предыдущий день">
          ←
        </button>
        <input
          className="input"
          type="date"
          value={day}
          max={today}
          onChange={(e) => e.target.value && setDay(e.target.value)}
          style={{ width: "auto" }}
        />
        <button
          className="btn sm ghost"
          disabled={day >= today}
          onClick={() => setDay(shiftDay(day, 1))}
          aria-label="Следующий день"
        >
          →
        </button>
        {day !== today && (
          <button className="btn sm ghost" onClick={() => setDay(today)}>Сегодня</button>
        )}
      </div>

      {error && <p className="muted mt-3">{error}</p>}

      {t && (
        <div className="grid stats stagger mt-4">
          {tiles.map((s) => (
            <div className="card" key={s.label}>
              <span className="tx-icon"><Icon name={s.icon as never} size={18} /></span>
              <div className="muted mt-3">{s.label}</div>
              <div className="stat-value">{s.value}</div>
            </div>
          ))}
        </div>
      )}

      <div className="tabs">
        <button
          className={"navlink" + (tab === "orders" ? " active" : "")}
          onClick={() => setTab("orders")}
        >
          <Icon name="receipt" size={16} /> Заказы{data ? ` · ${data.orders.length}` : ""}
        </button>
        <button
          className={"navlink" + (tab === "payments" ? " active" : "")}
          onClick={() => setTab("payments")}
        >
          <Icon name="wallet" size={16} /> Платежи{data ? ` · ${data.rows.length}` : ""}
        </button>
      </div>

      {data && tab === "orders" && (
        <div className="card">
          {data.orders.map((o) => (
            <div className="row" key={o.id} style={{ alignItems: "flex-start" }}>
              <span className="tx-icon"><Icon name="receipt" size={17} /></span>
              <div className="row-body">
                <strong>
                  {o.number != null ? `№${o.number}` : `Заказ ${o.id}`}
                  <span className="muted"> · {time(o.created_at)}</span>
                  {o.customer_name && <span className="muted"> · {o.customer_name}</span>}
                  {o.table && <span className="muted"> · стол {o.table}</span>}
                </strong>
                <span className="muted sm">{o.items.join(", ") || "—"}</span>
                <span className="wrap mt-1" style={{ gap: 6 }}>
                  <span className={"badge " + (ORDER_BADGE[o.status] ?? "open")}>{o.status_display}</span>
                  {o.channel && (
                    <span className="chip sm">{howPaid(o.channel, o.method, o.method_display)}</span>
                  )}
                  {Number(o.bonus_spent) > 0 && (
                    <span className="chip sm">бонусами −{money(o.bonus_spent)}</span>
                  )}
                  {o.fiscal_receipt && <span className="muted sm">чек: {o.fiscal_receipt}</span>}
                </span>
              </div>
              <strong className="num">{money(o.total)}</strong>
            </div>
          ))}
          {data.orders.length === 0 && <p className="muted">За этот день заказов нет.</p>}
        </div>
      )}

      {data && tab === "payments" && (
        <div className="card">
          {data.rows.map((p) => (
            <div className="row" key={p.id} style={{ alignItems: "flex-start" }}>
              <span className="tx-icon">
                <Icon name={p.refund ? "minus" : p.method === "cash" ? "cash" : "card"} size={17} />
              </span>
              <div className="row-body">
                <strong>
                  {p.refund ? "Возврат" : howPaid(p.channel, p.method, p.method_display) || p.channel_display}
                  <span className="muted"> · {time(p.created_at)}</span>
                </strong>
                <span className="muted sm">
                  {p.order_number != null ? `Заказ №${p.order_number}` : "Без заказа"}
                  {p.customer_name ? ` · ${p.customer_name}` : ""}
                  {p.channel !== "manual" ? ` · ${p.provider_display}` : ""}
                  {p.fiscal_receipt ? ` · чек: ${p.fiscal_receipt}` : ""}
                </span>
                <span className="mt-1">
                  <span className={"badge " + (p.refund ? "cancelled" : p.status === "succeeded" ? "paid" : p.status === "pending" ? "pending" : "cancelled")}>
                    {p.refund ? "Деньги возвращены" : p.status === "pending" && p.channel === "kassa" ? "Ждёт на кассе" : p.status_display}
                  </span>
                </span>
              </div>
              <strong className="num">{p.refund ? "−" : ""}{money(p.amount)}</strong>
            </div>
          ))}
          {data.rows.length === 0 && <p className="muted">За этот день платежей нет.</p>}
        </div>
      )}
    </>
  );
}
