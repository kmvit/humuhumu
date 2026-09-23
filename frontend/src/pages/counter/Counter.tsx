import { useCallback, useEffect, useMemo, useState } from "react";
import { get, patch, post, ApiError } from "../../api";
import type { Order, PayMethod, Performer } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";
import Compose from "../waiter/Compose";
import { useToast } from "../../components/ui/Toast";
import { fmtDuration, minutesBetween } from "../../time";
import { useSite } from "../../site";

function money(v: string | number | null | undefined): string {
  return Number(v ?? 0).toLocaleString("ru", { maximumFractionDigits: 2 });
}

type Stage = "unpaid" | "new" | "in_progress" | "ready";

/** Статус заказа целиком: на стойке один человек собирает и еду, и напитки. */
function stage(o: Order): Stage {
  // Неоплаченный заказ стоит перед всеми колонками: его не готовят,
  // пока не придут деньги.
  if (o.status === "unpaid") return "unpaid";
  const st = o.items.map((i) => i.status);
  if (st.length && st.every((s) => s === "ready")) return "ready";
  if (st.some((s) => s !== "new")) return "in_progress";
  return "new";
}

const COLUMNS: { key: Stage; label: string }[] = [
  { key: "unpaid", label: "Ждут оплаты" },
  { key: "new", label: "Новые" },
  { key: "in_progress", label: "Собираем" },
  { key: "ready", label: "Готов — выдать" },
];

export default function Counter() {
  const toast = useToast();
  const site = useSite();
  // Предоплата: заказы, ждущие денег, приезжают этой же доской —
  // отдельный поток разъехался бы с основным по времени опроса.
  const prepay = site?.prepay_required === true && site?.online_payment === true;
  const { orders, setOrders, highlight, reload } = useLiveOrders(
    prepay ? "/orders/?status=open&with_unpaid=1" : "/orders/?status=open"
  );
  const [busy, setBusy] = useState<number | null>(null);
  const [payFor, setPayFor] = useState<number | null>(null);
  const [cashFor, setCashFor] = useState<number | null>(null);
  // Кто сегодня в смене: заказ по QR приходит без исполнителя, а сделает
  // его кто-то из стоящих за стойкой — отметить это можно на карточке.
  const [performers, setPerformers] = useState<Performer[]>([]);

  useEffect(() => {
    // Ошибка не должна мешать работе: без списка заказы принимаются как прежде.
    get<Performer[]>("/shifts/performers/").then(setPerformers).catch(() => {});
  }, []);
  // Заказ на словах: гость подошёл к окну и назвал позиции. Столов на стойке
  // нет, поэтому Compose открываем без стола — он выдаст номер.
  const [composing, setComposing] = useState(false);

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

  /** Гость заплатил на кассе: деньги в реестр, заказ — в работу. */
  async function takeCash(order: Order, method: PayMethod) {
    setBusy(order.id);
    try {
      apply(await post<Order>(`/orders/${order.id}/prepaid/`, { pay_method: method }));
      setCashFor(null);
      toast(`Оплачен · ${money(order.total)} ₽ — заказ пошёл в работу`);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Не удалось принять оплату");
    } finally {
      setBusy(null);
    }
  }

  /** Отметить, кто из смены делает этот заказ. Пустое — снять отметку. */
  async function setPerformer(order: Order, id: number | "") {
    try {
      apply(await patch<Order>(`/orders/${order.id}/performer/`, { performer: id === "" ? null : id }));
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Не удалось записать исполнителя");
    }
  }

  const byStage = useMemo(() => {
    const map: Record<Stage, Order[]> = { unpaid: [], new: [], in_progress: [], ready: [] };
    // старые сверху: кто раньше заказал, того раньше и обслуживают
    [...orders]
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
      .forEach((o) => map[stage(o)].push(o));
    return map;
  }, [orders]);

  if (composing) {
    return (
      <Compose
        onCreated={() => {
          setComposing(false);
          reload();
        }}
        onCancel={() => setComposing(false)}
      />
    );
  }

  return (
    <>
      <div className="between">
        <h1 className="h1">Стойка</h1>
        <div className="inline tight">
          <span className="chip">
            <Icon name="spark" size={15} /> {orders.length}
          </span>
          <button className="btn sm" onClick={() => setComposing(true)}>
            <Icon name="plus" size={16} /> Новый заказ
          </button>
        </div>
      </div>
      <p className="muted subtitle">
        Гость заказывает по QR или на словах — соберите и выдайте по номеру
      </p>

      {orders.length === 0 ? (
        <p className="muted center mt-5">Заказов нет — всё выдано.</p>
      ) : (
        <div className="kanban">
          {COLUMNS.filter((c) => c.key !== "unpaid" || prepay).map((col) => (
            <div className="kanban-col" key={col.key}>
              <div className="kanban-head">
                <span>{col.label}</span>
                <span className="chip sm">{byStage[col.key].length}</span>
              </div>
              <div className="stack loose">
                {byStage[col.key].map((o) => (
                  <div className={"card" + (highlight.has(o.id) ? " new-order" : "")} key={o.id}>
                    <div className="between">
                      {/* Номер даётся при оплате, поэтому у ждущих его нет —
                          показываем имя гостя, по нему и найдём заказ. */}
                      <strong className="counter-no">
                        {o.daily_number != null ? `№${o.daily_number}` : (o.customer_name || "Без номера")}
                      </strong>
                      <span className="num">{money(o.total)} ₽</span>
                    </div>
                    {o.paid_at && col.key !== "unpaid" && (
                      <span className="badge paid mt-1">
                        <Icon name="check" size={13} /> Оплачен
                      </span>
                    )}
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
                            {it.options_text && (
                              <span className="opts block">{it.options_text}</span>
                            )}
                          </span>
                        </li>
                      ))}
                    </ul>

                    {performers.length > 1 && col.key !== "ready" && (
                      <select
                        className="input sm mt-2"
                        value={o.performer ?? ""}
                        onChange={(e) =>
                          setPerformer(o, e.target.value === "" ? "" : Number(e.target.value))
                        }
                      >
                        <option value="">Кто выполняет?</option>
                        {performers.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                            {p.in_shift ? "" : " — не в смене"}
                          </option>
                        ))}
                      </select>
                    )}
                    {col.key === "ready" && o.performer_name && (
                      <p className="muted sm m-0">Выполнил: {o.performer_name}</p>
                    )}
                    {col.key === "unpaid" &&
                      (cashFor === o.id ? (
                        <div className="grid cols-2 mt-2">
                          <button
                            className="btn sm"
                            disabled={busy === o.id}
                            onClick={() => takeCash(o, "cash")}
                          >
                            <Icon name="cash" size={16} /> Наличными
                          </button>
                          <button
                            className="btn sm"
                            disabled={busy === o.id}
                            onClick={() => takeCash(o, "card")}
                          >
                            <Icon name="card" size={16} /> Картой
                          </button>
                        </div>
                      ) : (
                        <>
                          <p className="muted sm m-0">
                            Гость платит на своём телефоне. Заплатил у кассы — отметьте,
                            и заказ пойдёт в работу.
                          </p>
                          <button className="btn sm block mt-2" onClick={() => setCashFor(o.id)}>
                            <Icon name="cash" size={16} /> Оплатил на кассе
                          </button>
                        </>
                      ))}
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
                    {col.key === "ready" && o.paid_at && (
                      <button
                        className="btn sm block"
                        disabled={busy === o.id}
                        onClick={() => handOut(o, o.pay_method)}
                      >
                        <Icon name="share" size={16} /> Выдать
                      </button>
                    )}
                    {col.key === "ready" && !o.paid_at &&
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
