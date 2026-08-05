import { useState } from "react";
import { patch } from "../../api";
import type { Order, OrderItem, Station, StationStatus } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";
import { fmtDuration, minutesBetween } from "../../time";

const COLUMNS: { key: StationStatus; label: string }[] = [
  { key: "new", label: "Новый" },
  { key: "in_progress", label: "В процессе" },
  { key: "ready", label: "Готов" },
];

// Канбан станции: карточка в колонке по агрегату, готовность — по каждой позиции.
export default function StationBoard({ station }: { station: Station }) {
  const isKitchen = station === "kitchen";
  const title = isKitchen ? "Кухня" : "Бар";
  const subtitle = isKitchen ? "Еда — отмечайте готовность позиций" : "Напитки — отмечайте готовность позиций";
  const empty = isKitchen ? "Нет заказов на кухне" : "Нет заказов в баре";
  const statusField: "food_status" | "drinks_status" = isKitchen ? "food_status" : "drinks_status";

  const { orders, setOrders, highlight } = useLiveOrders(`/orders/?station=${station}`);
  const [busy, setBusy] = useState<string | null>(null);

  function apply(updated: Order) {
    setOrders((os) => os.map((x) => (x.id === updated.id ? updated : x)));
  }

  async function setItem(order: Order, item: OrderItem, target: StationStatus) {
    setBusy(`i${item.id}`);
    try {
      apply(await patch<Order>(`/orders/${order.id}/item_status/`, { item_id: item.id, status: target }));
    } finally {
      setBusy(null);
    }
  }

  async function allReady(order: Order) {
    setBusy(`o${order.id}`);
    try {
      apply(await patch<Order>(`/orders/${order.id}/${statusField}/`, { status: "ready" }));
    } finally {
      setBusy(null);
    }
  }

  function itemControl(order: Order, it: OrderItem) {
    if (it.status === "ready") {
      return (
        <button
          className="icon-btn sm"
          title="Вернуть в работу"
          disabled={busy === `i${it.id}`}
          onClick={() => setItem(order, it, "in_progress")}
        >
          <Icon name="check" size={14} />
        </button>
      );
    }
    const next: StationStatus = it.status === "new" ? "in_progress" : "ready";
    return (
      <button
        className={"btn sm" + (it.status === "new" ? " ghost" : "")}
        disabled={busy === `i${it.id}`}
        onClick={() => setItem(order, it, next)}
      >
        {it.status === "new" ? "В работу" : "Готово"}
      </button>
    );
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
            // FIFO: старые заказы сверху, новые приходят снизу — чтобы не терялись
            const cards = orders
              .filter((o) => o[statusField] === col.key)
              .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
            return (
              <div className="kanban-col" key={col.key}>
                <div className="kanban-head">
                  <span>{col.label}</span>
                  <span className="chip sm">{cards.length}</span>
                </div>
                <div className="stack" style={{ gap: 10 }}>
                  {cards.map((o) => {
                    const its = o.items.filter((it) => it.station === station);
                    const notAllReady = its.some((it) => it.status !== "ready");
                    // тайминг именно этой станции: сколько ждёт / готовим / за сколько сделали
                    const startedAt = isKitchen ? o.food_started_at : o.drinks_started_at;
                    const readyAt = isKitchen ? o.food_ready_at : o.drinks_ready_at;
                    const timing =
                      col.key === "ready" && startedAt && readyAt
                        ? `готово за ${fmtDuration(minutesBetween(startedAt, readyAt))}`
                        : col.key === "in_progress" && startedAt
                        ? `готовим ${fmtDuration(minutesBetween(startedAt))}`
                        : `ждёт ${fmtDuration(minutesBetween(o.created_at))}`;
                    return (
                      <div className={"card" + (highlight.has(o.id) ? " new-order" : "")} key={o.id}>
                        <div className="between">
                          <strong style={{ fontFamily: "Fredoka", fontSize: 17 }}>№{o.id}</strong>
                          {o.table && <span className="badge open">Стол {o.table}</span>}
                        </div>
                        <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
                          <Icon name="spark" size={12} /> {timing}
                        </div>
                        {o.comment && (
                          <div className="order-note static" style={{ marginTop: 8 }}>
                            <Icon name="edit" size={13} /> {o.comment}
                          </div>
                        )}
                        <ul className="stack" style={{ gap: 6, margin: "10px 0", listStyle: "none", padding: 0 }}>
                          {its.map((it) => (
                            <li key={it.id} className="between" style={{ gap: 8 }}>
                              <span style={{ opacity: it.status === "ready" ? 0.55 : 1, textDecoration: it.status === "ready" ? "line-through" : "none" }}>
                                {it.product_name} <span className="num muted">× {it.quantity}</span>
                              </span>
                              {itemControl(o, it)}
                            </li>
                          ))}
                        </ul>
                        {its.length > 1 && notAllReady && (
                          <button
                            className="btn sm block"
                            disabled={busy === `o${o.id}`}
                            onClick={() => allReady(o)}
                          >
                            <Icon name="check" size={16} /> Готово всё
                          </button>
                        )}
                      </div>
                    );
                  })}
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
