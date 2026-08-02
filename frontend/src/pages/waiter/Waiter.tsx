import { useMemo, useState } from "react";
import { post } from "../../api";
import type { Order, OrderItem, StationStatus } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";
import { fmtDuration, minutesBetween } from "../../time";
import Compose from "./Compose";

const TABLES = Array.from({ length: 10 }, (_, i) => String(i + 1));

const STATUS_LABEL: Record<StationStatus, string> = {
  new: "новый",
  in_progress: "готовится",
  ready: "готово",
};
const STATUS_CLASS: Record<StationStatus, string> = {
  new: "open",
  in_progress: "preparing",
  ready: "ready",
};

export default function Waiter() {
  const { orders, reload } = useLiveOrders("/orders/?status=open", { sound: false });
  const [selected, setSelected] = useState<string | null>(null);
  const [composeFor, setComposeFor] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const [busyItem, setBusyItem] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);

  // подтверждение прямо в UI — нативный confirm() в киоск/встроенных браузерах подавляется
  async function removeItem(order: Order, item: OrderItem) {
    setBusyItem(item.id);
    try {
      await post(`/orders/${order.id}/remove_item/`, { item_id: item.id });
      setConfirmId(null);
      await reload();
    } finally {
      setBusyItem(null);
    }
  }

  const byTable = useMemo(() => {
    const m: Record<string, Order[]> = {};
    for (const o of orders) (m[o.table] ||= []).push(o);
    return m;
  }, [orders]);

  if (composeFor) {
    return (
      <Compose
        table={composeFor}
        onCreated={() => {
          setComposeFor(null);
          reload();
        }}
        onCancel={() => setComposeFor(null)}
      />
    );
  }

  const selOrders = selected ? byTable[selected] ?? [] : [];
  const selTotal = selOrders.reduce((s, o) => s + Number(o.total), 0);

  // разбивка суммы стола по гостям (0 = общий)
  const guestBreakdown = (() => {
    const map = new Map<number, number>();
    for (const o of selOrders)
      for (const it of o.items) {
        const g = it.guest ?? 0;
        map.set(g, (map.get(g) ?? 0) + Number(it.unit_price) * it.quantity);
      }
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  })();
  const hasGuests = guestBreakdown.some(([g]) => g !== 0);

  async function closeTable(table: string) {
    setClosing(true);
    try {
      await post("/orders/close_table/", { table });
      setSelected(null);
      await reload();
    } finally {
      setClosing(false);
    }
  }

  return (
    <>
      <h1 className="h1">Столы</h1>
      <p className="muted" style={{ marginTop: 4 }}>Выберите стол, чтобы создать заказ или закрыть счёт</p>

      <div className="grid" style={{ marginTop: 16, gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }}>
        {TABLES.map((t) => {
          const os = byTable[t] ?? [];
          const occupied = os.length > 0;
          const ready = occupied && os.every((o) => o.is_ready);
          const total = os.reduce((s, o) => s + Number(o.total), 0);
          return (
            <button
              key={t}
              className={"card hover table-tile" + (selected === t ? " sel" : "") + (occupied ? (ready ? " ready" : " busy") : " free")}
              onClick={() => setSelected(t)}
            >
              <strong style={{ fontFamily: "Fredoka", fontSize: 22 }}>{t}</strong>
              <span className="muted" style={{ fontSize: 12.5 }}>
                {occupied ? (ready ? "готов" : "готовится") : "свободен"}
              </span>
              {occupied && <span className="num" style={{ fontSize: 13 }}>{total.toLocaleString("ru")} ₽</span>}
            </button>
          );
        })}
      </div>

      {selected && (
        <section className="card enter" style={{ marginTop: 18 }}>
          <div className="between">
            <h2 style={{ fontFamily: "Fredoka", fontSize: 20 }}>Стол {selected}</h2>
            <button className="icon-btn" onClick={() => setSelected(null)} aria-label="Закрыть">
              <Icon name="plus" size={18} />
            </button>
          </div>

          {selOrders.length === 0 ? (
            <p className="muted" style={{ margin: "12px 0" }}>Стол свободен — заказов нет.</p>
          ) : (
            <div className="stack" style={{ gap: 12, margin: "12px 0" }}>
              {selOrders.map((o) => (
                <div key={o.id} style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                  <div className="between">
                    <strong>№{o.id} · <span className="num">{o.total}</span> ₽</strong>
                    <span className={"badge " + (o.is_ready ? "ready" : "preparing")}>
                      {o.is_ready ? "готов" : "готовится"}
                    </span>
                  </div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                    <Icon name="spark" size={12} /> {fmtDuration(minutesBetween(o.created_at))} · с открытия
                  </div>
                  <ul className="stack" style={{ gap: 3, margin: "8px 0 0", listStyle: "none", padding: 0 }}>
                    {o.items.map((it) => (
                      <li key={it.id} className="between">
                        <span>
                          {it.product_name}
                          <span className="muted" style={{ fontSize: 12 }}> · {it.station === "kitchen" ? "кухня" : "бар"}</span>
                          {it.guest && <span className="badge open" style={{ marginLeft: 6, padding: "1px 7px", fontSize: 11 }}>Гость {it.guest}</span>}
                        </span>
                        {confirmId === it.id ? (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                            <span className="muted" style={{ fontSize: 12.5 }}>Убрать?</span>
                            <button
                              className="icon-btn sm danger"
                              title="Да, убрать"
                              disabled={busyItem === it.id}
                              onClick={() => removeItem(o, it)}
                            >
                              <Icon name="check" size={14} />
                            </button>
                            <button
                              className="icon-btn sm"
                              title="Отмена"
                              onClick={() => setConfirmId(null)}
                            >
                              <span style={{ display: "inline-flex", transform: "rotate(45deg)" }}>
                                <Icon name="plus" size={14} />
                              </span>
                            </button>
                          </span>
                        ) : (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
                            <span className="num muted">× {it.quantity}</span>
                            <button
                              className="icon-btn sm danger"
                              title="Убрать позицию"
                              onClick={() => setConfirmId(it.id)}
                            >
                              <Icon name="minus" size={14} />
                            </button>
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                  <div className="wrap" style={{ marginTop: 8 }}>
                    {o.has_food && (
                      <span className={"badge " + STATUS_CLASS[o.food_status]}>Кухня: {STATUS_LABEL[o.food_status]}</span>
                    )}
                    {o.has_drinks && (
                      <span className={"badge " + STATUS_CLASS[o.drinks_status]}>Бар: {STATUS_LABEL[o.drinks_status]}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {selOrders.length > 0 && hasGuests && (
            <div style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
              <div className="muted" style={{ fontSize: 12.5, marginBottom: 6 }}>Счёт по гостям</div>
              <div className="stack" style={{ gap: 4 }}>
                {guestBreakdown.map(([g, sum]) => (
                  <div className="between" key={g}>
                    <span>{g === 0 ? "Общий" : `Гость ${g}`}</span>
                    <span className="num">{sum.toLocaleString("ru")} ₽</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="wrap" style={{ marginTop: 14 }}>
            <button className="btn sm" onClick={() => setComposeFor(selected)}>
              <Icon name="plus" size={16} /> Новый заказ
            </button>
            {selOrders.length > 0 && (
              <button
                className="btn sm ghost"
                onClick={() => closeTable(selected)}
                disabled={closing}
              >
                <Icon name="check" size={16} /> Закрыть счёт{selTotal ? ` · ${selTotal.toLocaleString("ru")} ₽` : ""}
              </button>
            )}
          </div>
        </section>
      )}
    </>
  );
}
