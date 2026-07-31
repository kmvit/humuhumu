import { useEffect, useState } from "react";
import { get } from "../../api";
import type { Order } from "../../types";
import Icon from "../../components/Icon";

export default function Orders() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    get<Order[]>("/orders/")
      .then(setOrders)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading)
    return (
      <>
        <h1 className="h1">Мои заказы</h1>
        <div className="stack" style={{ marginTop: 14 }}>
          <div className="skeleton" style={{ height: 90 }} />
          <div className="skeleton" style={{ height: 90 }} />
        </div>
      </>
    );

  return (
    <>
      <h1 className="h1">Мои заказы</h1>
      {orders.length === 0 && (
        <div className="card enter" style={{ marginTop: 14, textAlign: "center", padding: 40 }}>
          <span className="tx-icon" style={{ margin: "0 auto 10px", width: 52, height: 52 }}>
            <Icon name="receipt" size={26} />
          </span>
          <p className="muted">Заказов пока нет. Загляните в меню!</p>
        </div>
      )}
      <div className="stack stagger" style={{ marginTop: 14, gap: 12 }}>
        {orders.map((o) => (
          <div className="card hover" key={o.id}>
            <div className="between">
              <strong style={{ fontFamily: "Fredoka", fontSize: 18 }}>Заказ №{o.id}</strong>
              <span className={"badge " + o.status}>{o.status_display}</span>
            </div>
            <div className="stack" style={{ margin: "10px 0" }}>
              {o.items.map((it) => (
                <div className="between" key={it.id}>
                  <span>{it.product_name} × {it.quantity}</span>
                  <span className="muted num">{it.subtotal}</span>
                </div>
              ))}
            </div>
            <div className="between" style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
              <span className="muted">{o.pay_method === "tokens" ? "Токены" : "Карта"}</span>
              <strong className="num" style={{ fontSize: 17 }}>{o.total} ток.</strong>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
