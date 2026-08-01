import { useMemo, useState } from "react";
import { post } from "../../api";
import type { Order } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";
import Compose from "./Compose";

const TABLES = Array.from({ length: 10 }, (_, i) => String(i + 1));

export default function Waiter() {
  const { orders, reload } = useLiveOrders("/orders/?status=open", { sound: false });
  const [selected, setSelected] = useState<string | null>(null);
  const [composeFor, setComposeFor] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

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
                  <ul className="stack" style={{ gap: 3, margin: "8px 0 0", listStyle: "none", padding: 0 }}>
                    {o.items.map((it) => (
                      <li key={it.id} className="between">
                        <span>{it.product_name} <span className="muted" style={{ fontSize: 12 }}>· {it.station === "kitchen" ? "кухня" : "бар"}</span></span>
                        <span className="num muted">× {it.quantity}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="wrap" style={{ marginTop: 8 }}>
                    {o.has_food && (
                      <span className={"badge " + (o.food_ready ? "ready" : "preparing")}>Кухня: {o.food_ready ? "готово" : "готовится"}</span>
                    )}
                    {o.has_drinks && (
                      <span className={"badge " + (o.drinks_ready ? "ready" : "preparing")}>Бар: {o.drinks_ready ? "готово" : "готовится"}</span>
                    )}
                  </div>
                </div>
              ))}
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
