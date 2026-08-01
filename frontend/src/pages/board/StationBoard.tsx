import { useState } from "react";
import { patch } from "../../api";
import type { Order, Station, StationStatus } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";

const COLUMNS: { key: StationStatus; label: string }[] = [
  { key: "new", label: "Новый" },
  { key: "in_progress", label: "В процессе" },
  { key: "ready", label: "Готов" },
];

// Канбан-доска станции: три колонки, карточки двигаются вперёд/назад.
// Одна и та же для кухни и бара — отличается станцией и полем статуса.
export default function StationBoard({ station }: { station: Station }) {
  const isKitchen = station === "kitchen";
  const title = isKitchen ? "Кухня" : "Бар";
  const subtitle = isKitchen ? "Еда — двигайте по статусам" : "Напитки — двигайте по статусам";
  const empty = isKitchen ? "Нет заказов на кухне" : "Нет заказов в баре";
  const statusField: "food_status" | "drinks_status" = isKitchen ? "food_status" : "drinks_status";

  const { orders, setOrders, highlight } = useLiveOrders(`/orders/?station=${station}`);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function move(o: Order, target: StationStatus) {
    setBusyId(o.id);
    try {
      await patch(`/orders/${o.id}/${statusField}/`, { status: target });
      setOrders((os) => os.map((x) => (x.id === o.id ? { ...x, [statusField]: target } : x)));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="between">
        <h1 className="h1">{title}</h1>
        <span className="chip"><Icon name="spark" size={15} /> {orders.length}</span>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>{subtitle}</p>

      {orders.length === 0 ? (
        <p className="muted" style={{ marginTop: 32, textAlign: "center" }}>{empty}</p>
      ) : (
        <div className="kanban">
          {COLUMNS.map((col) => {
            const cards = orders.filter((o) => o[statusField] === col.key);
            return (
              <div className="kanban-col" key={col.key}>
                <div className="kanban-head">
                  <span>{col.label}</span>
                  <span className="chip sm">{cards.length}</span>
                </div>
                <div className="stack" style={{ gap: 10 }}>
                  {cards.map((o) => (
                    <div className={"card" + (highlight.has(o.id) ? " new-order" : "")} key={o.id}>
                      <div className="between">
                        <strong style={{ fontFamily: "Fredoka", fontSize: 17 }}>№{o.id}</strong>
                        {o.table && <span className="badge open">Стол {o.table}</span>}
                      </div>
                      <ul className="stack" style={{ gap: 3, margin: "10px 0", listStyle: "none", padding: 0 }}>
                        {o.items.filter((it) => it.station === station).map((it) => (
                          <li key={it.id} className="between">
                            <span>{it.product_name}</span>
                            <span className="num muted">× {it.quantity}</span>
                          </li>
                        ))}
                      </ul>
                      <div className="wrap">
                        {col.key !== "new" && (
                          <button
                            className="btn sm ghost"
                            disabled={busyId === o.id}
                            onClick={() => move(o, col.key === "ready" ? "in_progress" : "new")}
                          >
                            <Icon name="minus" size={15} /> Назад
                          </button>
                        )}
                        {col.key !== "ready" && (
                          <button
                            className="btn sm"
                            disabled={busyId === o.id}
                            onClick={() => move(o, col.key === "new" ? "in_progress" : "ready")}
                          >
                            {col.key === "new" ? "В работу" : "Готово"} <Icon name="check" size={15} />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                  {cards.length === 0 && (
                    <p className="muted" style={{ fontSize: 13, textAlign: "center", padding: "6px 0" }}>—</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
