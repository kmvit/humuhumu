import { useEffect, useState, type FormEvent } from "react";
import { get, patch, post, ApiError } from "../../api";
import type { Order, OrderStatus, TokenPackage } from "../../types";
import Icon from "../../components/Icon";

const NEXT: { value: OrderStatus; label: string }[] = [
  { value: "preparing", label: "Готовится" },
  { value: "ready", label: "Готов" },
  { value: "done", label: "Выдан" },
  { value: "cancelled", label: "Отмена" },
];

export default function Cashier() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [packages, setPackages] = useState<TokenPackage[]>([]);
  const [clientId, setClientId] = useState("");
  const [packageId, setPackageId] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const loadOrders = () => get<Order[]>("/orders/").then(setOrders).catch(() => {});

  useEffect(() => {
    loadOrders();
    get<TokenPackage[]>("/token-packages/").then(setPackages).catch(() => {});
  }, []);

  async function changeStatus(id: number, status: OrderStatus) {
    await patch(`/orders/${id}/set_status/`, { status });
    loadOrders();
  }

  async function topup(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    try {
      const res = await post<{ balance: string }>("/wallet/topup/", {
        client: Number(clientId),
        package: Number(packageId),
      });
      setMsg(`Готово! Новый баланс клиента: ${res.balance} ток.`);
      setClientId("");
      setPackageId("");
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : "Ошибка пополнения");
    }
  }

  return (
    <>
      <h1 className="h1">Касса</h1>

      <div className="card hover enter" style={{ marginTop: 14 }}>
        <div className="between" style={{ marginBottom: 14 }}>
          <h2 style={{ fontSize: 18 }}>Пополнить токены</h2>
          <span className="tx-icon"><Icon name="wallet" size={18} /></span>
        </div>
        <form onSubmit={topup}>
          <div className="field">
            <label className="label">ID клиента</label>
            <input className="input" value={clientId} onChange={(e) => setClientId(e.target.value)} inputMode="numeric" required />
          </div>
          <div className="field">
            <label className="label">Пакет</label>
            <select className="input" value={packageId} onChange={(e) => setPackageId(e.target.value)} required>
              <option value="">— выберите —</option>
              {packages.map((p) => (
                <option key={p.id} value={p.id}>{p.pay_amount} ₽ → {p.total_tokens} ток.</option>
              ))}
            </select>
          </div>
          <button className="btn block" type="submit"><Icon name="gift" size={18} /> Начислить</button>
          {msg && <p className="success-msg" style={{ marginTop: 10 }}>{msg}</p>}
          {err && <p className="error" style={{ marginTop: 10 }}>{err}</p>}
        </form>
      </div>

      <h2 className="section-title">Заказы</h2>
      <div className="stack stagger" style={{ gap: 12 }}>
        {orders.map((o) => (
          <div className="card hover" key={o.id}>
            <div className="between">
              <strong style={{ fontFamily: "Fredoka", fontSize: 17 }}>
                №{o.id} · <span className="num">{o.total}</span> ток.
              </strong>
              <span className={"badge " + o.status}>{o.status_display}</span>
            </div>
            <p className="muted" style={{ margin: "8px 0" }}>
              {o.items.map((it) => `${it.product_name}×${it.quantity}`).join(", ")}
              {" · "}{o.pay_method === "tokens" ? "токены" : "карта"}
            </p>
            <div className="wrap">
              {NEXT.map((s) => (
                <button key={s.value} className="btn sm ghost" onClick={() => changeStatus(o.id, s.value)}>
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
