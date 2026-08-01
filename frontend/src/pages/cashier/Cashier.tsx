import { useState } from "react";
import { patch } from "../../api";
import type { PayMethod } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";

export default function Cashier() {
  const { orders, setOrders, highlight } = useLiveOrders("ready");
  const [busyId, setBusyId] = useState<number | null>(null);

  async function markPaid(id: number, pay_method: PayMethod) {
    setBusyId(id);
    try {
      await patch(`/orders/${id}/set_status/`, { status: "paid", pay_method });
      setOrders((os) => os.filter((o) => o.id !== id));
    } finally {
      setBusyId(null);
    }
  }

  async function cancel(id: number) {
    setBusyId(id);
    try {
      await patch(`/orders/${id}/set_status/`, { status: "cancelled" });
      setOrders((os) => os.filter((o) => o.id !== id));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="between">
        <h1 className="h1">Касса-бар</h1>
        <span className="chip"><Icon name="spark" size={15} /> {orders.length}</span>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>Готовые заказы — принять оплату</p>

      {orders.length === 0 ? (
        <p className="muted" style={{ marginTop: 32, textAlign: "center" }}>
          Нет готовых заказов
        </p>
      ) : (
        <div className="grid" style={{ marginTop: 16, gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
          {orders.map((o) => (
            <div className={"card hover" + (highlight.has(o.id) ? " new-order" : "")} key={o.id}>
              <div className="between">
                <strong style={{ fontFamily: "Fredoka", fontSize: 18 }}>
                  №{o.id} · <span className="num">{o.total}</span> ₽
                </strong>
                {o.table && <span className="badge ready">Стол {o.table}</span>}
              </div>
              <ul className="stack" style={{ gap: 4, margin: "12px 0", listStyle: "none", padding: 0 }}>
                {o.items.map((it) => (
                  <li key={it.id} className="between">
                    <span>{it.product_name}</span>
                    <span className="num muted">× {it.quantity}</span>
                  </li>
                ))}
              </ul>
              <div className="stack" style={{ gap: 8 }}>
                <div className="wrap">
                  <button
                    className="btn sm"
                    onClick={() => markPaid(o.id, "cash")}
                    disabled={busyId === o.id}
                  >
                    <Icon name="check" size={16} /> Наличные
                  </button>
                  <button
                    className="btn sm"
                    onClick={() => markPaid(o.id, "card")}
                    disabled={busyId === o.id}
                  >
                    <Icon name="wallet" size={16} /> Карта
                  </button>
                </div>
                <button
                  className="btn sm ghost"
                  onClick={() => cancel(o.id)}
                  disabled={busyId === o.id}
                >
                  Отмена
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
