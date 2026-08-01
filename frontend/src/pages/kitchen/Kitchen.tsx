import { useState } from "react";
import { patch } from "../../api";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";

export default function Kitchen() {
  const { orders, setOrders, highlight } = useLiveOrders("preparing");
  const [busyId, setBusyId] = useState<number | null>(null);

  async function markReady(id: number) {
    setBusyId(id);
    try {
      await patch(`/orders/${id}/set_status/`, { status: "ready" });
      setOrders((os) => os.filter((o) => o.id !== id));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="between">
        <h1 className="h1">Кухня</h1>
        <span className="chip"><Icon name="spark" size={15} /> {orders.length}</span>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>Заказы в работе</p>

      {orders.length === 0 ? (
        <p className="muted" style={{ marginTop: 32, textAlign: "center" }}>
          Нет заказов на кухне
        </p>
      ) : (
        <div className="grid" style={{ marginTop: 16, gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
          {orders.map((o) => (
            <div className={"card hover" + (highlight.has(o.id) ? " new-order" : "")} key={o.id}>
              <div className="between">
                <strong style={{ fontFamily: "Fredoka", fontSize: 18 }}>№{o.id}</strong>
                {o.table && <span className="badge preparing">Стол {o.table}</span>}
              </div>
              <ul className="stack" style={{ gap: 4, margin: "12px 0", listStyle: "none", padding: 0 }}>
                {o.items.map((it) => (
                  <li key={it.id} className="between">
                    <span>{it.product_name}</span>
                    <span className="num muted">× {it.quantity}</span>
                  </li>
                ))}
              </ul>
              <button
                className="btn block"
                onClick={() => markReady(o.id)}
                disabled={busyId === o.id}
              >
                <Icon name="check" size={18} /> Готово
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
