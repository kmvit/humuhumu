import { useCallback, useMemo, useState } from "react";
import { patch, post, ApiError } from "../../api";
import type { Order, PayMethod } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";
import { useToast } from "../../components/ui/Toast";
import { fmtDuration, minutesBetween } from "../../time";

function money(v: string | number | null | undefined): string {
  return Number(v ?? 0).toLocaleString("ru", { maximumFractionDigits: 2 });
}

/** Статус заказа целиком: на стойке один человек собирает и еду, и напитки. */
function stage(o: Order): "new" | "in_progress" | "ready" {
  const st = o.items.map((i) => i.status);
  if (st.length && st.every((s) => s === "ready")) return "ready";
  if (st.some((s) => s !== "new")) return "in_progress";
  return "new";
}

const COLUMNS: { key: "new" | "in_progress" | "ready"; label: string }[] = [
  { key: "new", label: "Новые" },
  { key: "in_progress", label: "Собираем" },
  { key: "ready", label: "Готов — выдать" },
];

export default function Counter() {
  const toast = useToast();
  const { orders, setOrders, highlight } = useLiveOrders("/orders/?status=open");
  const [busy, setBusy] = useState<number | null>(null);
  const [payFor, setPayFor] = useState<number | null>(null);

  const apply = useCallback(
    (updated: Order) => setOrders((os) => os.map((o) => (o.id === updated.id ? updated : o))),
    [setOrders]
  );

  async function move(order: Order, status: "in_progress" | "ready") {
    setBusy(order.id);
    try {
      apply(await patch<Order>(`/orders/${order.id}/work_status/`, { status }));
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Не удалось обновить заказ");
    } finally {
      setBusy(null);
    }
  }

  /** «Выдал» = закрыть заказ и зафиксировать оплату. */
  async function handOut(order: Order, method: PayMethod) {
    setBusy(order.id);
    try {
      await post(`/orders/${order.id}/close/`, { pay_method: method });
      setOrders((os) => os.filter((o) => o.id !== order.id));
      setPayFor(null);
      toast(`Заказ №${order.daily_number ?? order.id} выдан · ${money(order.total)} ₽`);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Не удалось закрыть заказ");
    } finally {
      setBusy(null);
    }
  }

  const byStage = useMemo(() => {
    const map: Record<string, Order[]> = { new: [], in_progress: [], ready: [] };
    // старые сверху: кто раньше заказал, того раньше и обслуживают
    [...orders]
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
      .forEach((o) => map[stage(o)].push(o));
    return map;
  }, [orders]);

  return (
    <>
      <div className="between">
        <h1 className="h1">Стойка</h1>
        <span className="chip">
          <Icon name="spark" size={15} /> {orders.length}
        </span>
      </div>
      <p className="muted subtitle">
        Заказы приходят из меню по QR — соберите и выдайте по номеру
      </p>

      {orders.length === 0 ? (
        <p className="muted center mt-5">Заказов нет — всё выдано.</p>
      ) : (
        <div className="kanban">
          {COLUMNS.map((col) => (
            <div className="kanban-col" key={col.key}>
              <div className="kanban-head">
                <span>{col.label}</span>
                <span className="chip sm">{byStage[col.key].length}</span>
              </div>
              <div className="stack loose">
                {byStage[col.key].map((o) => (
                  <div className={"card" + (highlight.has(o.id) ? " new-order" : "")} key={o.id}>
                    <div className="between">
                      <strong className="counter-no">№{o.daily_number ?? o.id}</strong>
                      <span className="num">{money(o.total)} ₽</span>
                    </div>
                    <div className="muted sm mt-1">
                      <Icon name="spark" size={12} />{" "}
                      {fmtDuration(minutesBetween(o.created_at))}
                      {o.customer_name ? ` · ${o.customer_name}` : ""}
                    </div>
                    {o.comment && (
                      <div className="order-note static mt-2">
                        <Icon name="edit" size={13} /> {o.comment}
                      </div>
                    )}
                    <ul className="stack tight list my-3">
                      {o.items.map((it) => (
                        <li key={it.id} className="between">
                          <span>
                            {it.product_name} <span className="num muted">× {it.quantity}</span>
                          </span>
                        </li>
                      ))}
                    </ul>

                    {col.key === "new" && (
                      <button
                        className="btn sm block"
                        disabled={busy === o.id}
                        onClick={() => move(o, "in_progress")}
                      >
                        Взять в работу
                      </button>
                    )}
                    {col.key === "in_progress" && (
                      <button
                        className="btn sm block"
                        disabled={busy === o.id}
                        onClick={() => move(o, "ready")}
                      >
                        <Icon name="check" size={16} /> Готов
                      </button>
                    )}
                    {col.key === "ready" &&
                      (payFor === o.id ? (
                        <div className="grid cols-2 mt-2">
                          <button
                            className="btn sm"
                            disabled={busy === o.id}
                            onClick={() => handOut(o, "cash")}
                          >
                            <Icon name="cash" size={16} /> Наличными
                          </button>
                          <button
                            className="btn sm"
                            disabled={busy === o.id}
                            onClick={() => handOut(o, "card")}
                          >
                            <Icon name="card" size={16} /> Картой
                          </button>
                        </div>
                      ) : (
                        <button className="btn sm block" onClick={() => setPayFor(o.id)}>
                          <Icon name="share" size={16} /> Выдать
                        </button>
                      ))}
                  </div>
                ))}
                {byStage[col.key].length === 0 && (
                  <p className="muted sm center" style={{ padding: "6px 0" }}>—</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
