import { useEffect, useMemo, useState } from "react";
import { get } from "../../api";
import type { Order } from "../../types";
import Icon from "../../components/Icon";

export default function Admin() {
  const [orders, setOrders] = useState<Order[]>([]);

  useEffect(() => {
    get<Order[]>("/orders/").then(setOrders).catch(() => {});
  }, []);

  const revenue = useMemo(
    () => orders.filter((o) => o.status !== "cancelled").reduce((s, o) => s + Number(o.total), 0),
    [orders]
  );
  const active = orders.filter((o) => o.status === "open").length;

  const stats = [
    { icon: "receipt", label: "Всего заказов", value: orders.length },
    { icon: "chart", label: "Оборот (без отмен)", value: revenue.toLocaleString("ru") },
    { icon: "store", label: "В работе", value: active },
  ] as const;

  return (
    <>
      <h1 className="h1">Админ-панель</h1>

      <div className="grid stats stagger" style={{ marginTop: 16 }}>
        {stats.map((s) => (
          <div className="card hover" key={s.label}>
            <span className="tx-icon"><Icon name={s.icon} size={18} /></span>
            <div className="muted" style={{ marginTop: 10 }}>{s.label}</div>
            <div className="num" style={{ fontFamily: "Fredoka", fontWeight: 700, fontSize: 30, color: "var(--brand)" }}>
              {s.value}
            </div>
          </div>
        ))}
      </div>

      <div className="card hover enter" style={{ marginTop: 16 }}>
        <div className="between">
          <div>
            <strong style={{ fontFamily: "Fredoka", fontSize: 17 }}>Управление каталогом</strong>
            <p className="muted" style={{ margin: "4px 0 0" }}>Товары и категории</p>
          </div>
          <a className="btn sm" href="http://localhost:8000/admin/" target="_blank" rel="noreferrer">
            Открыть <Icon name="arrowUp" size={15} />
          </a>
        </div>
      </div>

      <h2 className="section-title">Последние заказы</h2>
      <div className="card">
        {orders.slice(0, 20).map((o) => (
          <div className="row" key={o.id}>
            <span className="tx-icon"><Icon name="receipt" size={17} /></span>
            <div className="stack" style={{ gap: 2, flex: 1 }}>
              <strong>Заказ №{o.id}</strong>
              <span className="muted">{o.items.length} поз.{o.table ? ` · стол ${o.table}` : ""}</span>
            </div>
            <span className={"badge " + o.status}>{o.status_display}</span>
            <strong className="num">{o.total}</strong>
          </div>
        ))}
        {orders.length === 0 && <p className="muted">Заказов пока нет.</p>}
      </div>
    </>
  );
}
