import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import { get, patch, post, ApiError } from "../../api";
import type { Order, PayMethod, Performer } from "../../types";
import Icon from "../../components/Icon";
import { useLiveOrders } from "../../useLiveOrders";
import Compose from "../waiter/Compose";
import PayAtKassa from "./PayAtKassa";
import { useToast } from "../../components/ui/Toast";
import { fmtDuration, minutesBetween } from "../../time";
import { useFeature, useSite } from "../../site";
import BonusPanel from "../waiter/BonusPanel";
import KassaPaid from "./KassaPaid";

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
  // Гостю должно быть чем заплатить: онлайн или на кассе (см. prepay_required
  // на бэке) — иначе колонка «Ждут оплаты» так и стоит пустой.
  const kassa = site?.kassa_payment === true;
  // Бариста указывает гостя по телефону — бонусы копятся и без списания.
  const bonusOn = useFeature("loyalty") && !!site?.bonus_enabled;
  const prepay =
    site?.prepay_required === true && (site?.online_payment === true || kassa);
  // С кассой «Ждут оплаты» нужна всегда: туда же встаёт и заказ, который
  // бариста принял на словах, — он тоже оплачивается на кассе.
  const waiting = prepay || kassa;
  const { orders, setOrders, highlight, reload } = useLiveOrders(
    waiting ? "/orders/?status=open&with_unpaid=1" : "/orders/?status=open",
    // Сигнал — на оплаченный заказ, а не на оформленный: у окна один
    // человек, и звать его к кофемашине надо, когда пришли деньги.
    { alertWhen: useCallback((o: Order) => o.status !== "unpaid", []) }
  );
  const [busy, setBusy] = useState<number | null>(null);
  const [payFor, setPayFor] = useState<number | null>(null);
  const [cashFor, setCashFor] = useState<number | null>(null);
  // Заказ на кассе сам не отменяется — ушедшего гостя отменяет бариста.
  // Подтверждение в карточке: нативный confirm в киоск-браузере глушится.
  const [dropFor, setDropFor] = useState<number | null>(null);
  // Экран оплаты: какой заказ сейчас пробивают на кассе. Сам заказ берём
  // с доски — она и приносит весть об оплате.
  const [payingId, setPayingId] = useState<number | null>(null);
  // Последняя известная версия: опрос, начатый до создания заказа, может
  // вернуться позже и прийти без него — экран не должен из-за этого мигнуть
  // и закрыться. Пропал насовсем (отменили) — закрываем мы сами.
  const lastPaying = useRef<Order | null>(null);
  const found = payingId == null ? null : orders.find((o) => o.id === payingId) ?? null;
  if (found) lastPaying.current = found;
  const payingOrder = payingId == null ? null : found ?? lastPaying.current;
  // Пока бариста ждёт оплату у окна, доску перечитываем чаще: гость с
  // картой не должен стоять лишние десять секунд.
  useEffect(() => {
    if (payingId == null) return;
    const t = window.setInterval(reload, 2000);
    return () => window.clearInterval(t);
  }, [payingId, reload]);
  const closePaying = useCallback(() => setPayingId(null), []);
  // Кто сегодня в смене: заказ по QR приходит без исполнителя, а сделает
  // его кто-то из стоящих за стойкой — отметить это можно на карточке.
  const [performers, setPerformers] = useState<Performer[]>([]);
  // выданные за сегодня — экран возврата: у стойки это единственное место,
  // где бариста может найти уже закрытый заказ
  const [showClosed, setShowClosed] = useState(false);
  const [closed, setClosed] = useState<Order[]>([]);
  const [refundFor, setRefundFor] = useState<number | null>(null);
  const [refundStock, setRefundStock] = useState(false);

  useEffect(() => {
    // Ошибка не должна мешать работе: без списка заказы принимаются как прежде.
    get<Performer[]>("/shifts/performers/").then(setPerformers).catch(() => {});
  }, []);

  const loadClosed = useCallback(async () => {
    setClosed(await get<Order[]>("/orders/?status=paid&closed=today&with_refunded=1").catch(() => []));
  }, []);

  useEffect(() => {
    if (showClosed) loadClosed();
  }, [showClosed, loadClosed]);

  /** Вернуть гостю деньги. Карту возвращает банк, наличные — из ящика. */
  async function refundOrder(order: Order) {
    setBusy(order.id);
    try {
      const updated = await post<Order>(`/orders/${order.id}/refund/`, {
        return_to_stock: refundStock,
      });
      setClosed((os) => os.map((o) => (o.id === updated.id ? updated : o)));
      setRefundFor(null);
      setRefundStock(false);
      toast(`Возврат ${money(order.total)} ₽ проведён`);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Не удалось вернуть деньги");
    } finally {
      setBusy(null);
    }
  }
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

  /** Положить заказ на кассу: гость подошёл к окну, а сам кнопку не нажал. */
  async function sendToKassa(order: Order) {
    setBusy(order.id);
    try {
      apply(await post<Order>(`/orders/${order.id}/pay_terminal/`, {}));
      toast(`Заказ на кассе под номером ${order.id}`);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Не удалось отправить на кассу");
    } finally {
      setBusy(null);
    }
  }

  /** Гость ушёл, не заплатив: отменить заказ — он снимется и с кассы. */
  async function cancelOrder(order: Order) {
    setBusy(order.id);
    try {
      const updated = await patch<Order>(`/orders/${order.id}/cancel/`, {});
      setDropFor(null);
      if (updated.status === "cancelled") {
        setOrders((os) => os.filter((o) => o.id !== order.id));
        toast(`Заказ №${order.id} отменён и снят с кассы`);
      } else {
        // Касса успела сказать, что заказ оплачен, — он уже в работе.
        apply(updated);
      }
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Не удалось отменить заказ");
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
        // Стойка с кассой: сперва оплата, потом готовим — поэтому и кнопка
        // говорит, что будет дальше.
        submitLabel={kassa ? "На оплату" : undefined}
        // Гость с бонусами — до создания заказа: на кассу заказ уходит
        // сразу, и списание после касса бы уже не увидела.
        checkout={
          bonusOn
            ? { label: kassa ? "На кассу" : "Отправить", canRedeem: !!site?.bonus_redeem_waiter }
            : undefined
        }
        onCreated={(created) => {
          setComposing(false);
          if (created?.status === "unpaid") {
            // Кладём заказ на доску сразу, не дожидаясь опроса: иначе экран
            // оплаты открылся бы на пустом месте и тут же закрылся.
            setOrders((os) => [...os.filter((o) => o.id !== created.id), created]);
            lastPaying.current = created;
            setPayingId(created.id);
          }
          reload();
        }}
        onCancel={() => setComposing(false)}
      />
    );
  }

  return (
    <>
      {payingId != null && (
        <PayAtKassa
          order={payingOrder}
          paidFallback={
            payingOrder?.kassa_waiting ? <KassaPaid order={payingOrder} onDone={reload} /> : null
          }
          bonus={
            bonusOn && payingOrder ? (
              <BonusPanel
                order={payingOrder}
                onDone={reload}
                canRedeem={!!site?.bonus_redeem_waiter}
              />
            ) : null
          }
          busy={busy === payingId}
          onClose={closePaying}
          onResend={sendToKassa}
          onCancelOrder={async (o) => {
            await cancelOrder(o);
            setPayingId(null);
          }}
        />
      )}
      <div className="between">
        <h1 className="h1">Стойка</h1>
        <div className="inline tight">
          <span className="chip">
            <Icon name="spark" size={15} /> {orders.length}
          </span>
          <button
            className={"btn sm" + (showClosed ? "" : " ghost")}
            onClick={() => setShowClosed((v) => !v)}
          >
            <Icon name="receipt" size={16} /> Выданные
          </button>
          <button className="btn sm" onClick={() => setComposing(true)}>
            <Icon name="plus" size={16} /> Новый заказ
          </button>
        </div>
      </div>
      <p className="muted subtitle">
        Гость заказывает по QR или на словах — соберите и выдайте по номеру
      </p>

      {showClosed ? (
        <div className="stack loose mt-4">
          {closed.length === 0 ? (
            <p className="muted center mt-5">Сегодня выданных заказов нет</p>
          ) : (
            closed.map((o) => (
              <div className="card" key={o.id}>
                <div className="between">
                  <strong className="counter-no">№{o.daily_number ?? o.id}</strong>
                  <span className="inline tight">
                    {o.refunded_at ? (
                      <span className="badge cancelled">Возврат</span>
                    ) : (
                      <span className="badge">
                        <Icon name={o.pay_method === "card" ? "card" : "cash"} size={12} />{" "}
                        {o.pay_method_display}
                      </span>
                    )}
                    <span className="num">{money(o.payable)} ₽</span>
                  </span>
                </div>
                <div className="muted sm mt-1">
                  {o.customer_name ? `${o.customer_name} · ` : ""}
                  {o.items.map((it) => `${it.product_name} × ${it.quantity}`).join(", ")}
                </div>

                {!o.refunded_at &&
                  (refundFor === o.id ? (
                    <div className="rule-top mt-3 pt-3">
                      <label className="inline tight">
                        <input
                          type="checkbox"
                          checked={refundStock}
                          onChange={(e) => setRefundStock(e.target.checked)}
                        />
                        <span className="sm">Вернуть продукты на склад</span>
                      </label>
                      <p className="muted sm m-0 mt-1">
                        Не успели приготовить — продукты целы. Готовый напиток возвращать
                        на склад не нужно.
                      </p>
                      <div className="wrap mt-2">
                        <button
                          className="btn sm danger"
                          disabled={busy === o.id}
                          onClick={() => refundOrder(o)}
                        >
                          <Icon name="check" size={15} /> Да, вернуть {money(o.payable)} ₽
                        </button>
                        <button
                          className="btn sm ghost"
                          onClick={() => { setRefundFor(null); setRefundStock(false); }}
                        >
                          Отмена
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button className="btn sm ghost block mt-2" onClick={() => setRefundFor(o.id)}>
                      <Icon name="cash" size={15} /> Вернуть деньги
                    </button>
                  ))}
              </div>
            ))
          )}
        </div>
      ) : orders.length === 0 ? (
        <p className="muted center mt-5">Заказов нет — всё выдано.</p>
      ) : (
        <div className="kanban">
          {COLUMNS.filter((c) => c.key !== "unpaid" || waiting).map((col) => (
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
                        {o.daily_number != null ? `№${o.daily_number}` : (o.customer_name || `Заказ ${o.id}`)}
                      </strong>
                      {/* Сумма к оплате, а не стоимость: часть могли закрыть
                          бонусами, и бариста взял бы с гостя лишнее. */}
                      <span className="num">{money(o.payable)} ₽</span>
                    </div>
                    {Number(o.bonus_spent) > 0 && (
                      <div className="muted sm">
                        из {money(o.total)} ₽ · {money(o.bonus_spent)} бонусами
                      </div>
                    )}
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
                    {/* Бонусы меняют сумму к оплате — панель нужна, пока деньги
                        не взяты. У оплаченного заказа сумму уже не изменить:
                        просто показываем, за кем он, чтобы не спрашивать снова. */}
                    {bonusOn && !o.paid_at && (
                      <BonusPanel
                        order={o}
                        onDone={reload}
                        canRedeem={!!site?.bonus_redeem_waiter}
                      />
                    )}
                    {bonusOn && o.paid_at && o.bonus_guest && (
                      <p className="muted sm m-0 mt-2">
                        <Icon name="gift" size={13} /> {o.bonus_guest.name} ·{" "}
                        {Number(o.bonus_guest.balance).toLocaleString("ru")} б.
                      </p>
                    )}
                    {col.key === "ready" && o.performer_name && (
                      <p className="muted sm m-0">Выполнил: {o.performer_name}</p>
                    )}
                    {col.key === "unpaid" &&
                      (cashFor === o.id ? (
                        // Друг под другом: колонка доски узкая, и две
                        // кнопки в ряд вылезали за край карточки.
                        <div className="stack tight mt-2">
                          <button
                            className="btn sm block"
                            disabled={busy === o.id}
                            onClick={() => takeCash(o, "cash")}
                          >
                            <Icon name="cash" size={16} /> Наличными
                          </button>
                          <button
                            className="btn sm block"
                            disabled={busy === o.id}
                            onClick={() => takeCash(o, "card")}
                          >
                            <Icon name="card" size={16} /> Картой
                          </button>
                        </div>
                      ) : o.kassa_waiting ? (
                        // Оплату подтверждает касса — заказ сам уедет в
                        // «Новые». «Оплачено» — подстраховка, если касса не
                        // ответила: только для заказа, который на ней лежит.
                        <>
                          {/* Номер — в заголовке карточки, плашка его только повторяла. */}
                          <p className="muted sm m-0">
                            Лежит на кассе — пробейте, и заказ сам уйдёт в работу.
                          </p>
                          <button className="btn sm block mt-2" onClick={() => setPayingId(o.id)}>
                            <Icon name="cash" size={16} /> Экран оплаты
                          </button>
                          {dropFor === o.id ? (
                            <div className="wrap mt-2">
                              <span className="muted sm" style={{ alignSelf: "center" }}>
                                Отменить и снять с кассы?
                              </span>
                              <button
                                className="btn sm danger"
                                disabled={busy === o.id}
                                onClick={() => cancelOrder(o)}
                              >
                                Да, отменить
                              </button>
                              <button className="btn sm ghost" onClick={() => setDropFor(null)}>
                                Нет
                              </button>
                            </div>
                          ) : (
                            // «Оплачено» и «Отменить» — в одну строку: два исхода
                            // заказа у окна, бариста выбирает один из них.
                            <KassaPaid
                              order={o}
                              onDone={reload}
                              beside={
                                <button
                                  className="btn sm danger block"
                                  onClick={() => setDropFor(o.id)}
                                >
                                  <Icon name="close" size={15} /> Отменить
                                </button>
                              }
                            />
                          )}
                        </>
                      ) : kassa ? (
                        <>
                          <p className="muted sm m-0">
                            Гость платит на телефоне или на кассе. Подошёл к окну — отправьте
                            заказ на кассу.
                          </p>
                          <button
                            className="btn sm block mt-2"
                            disabled={busy === o.id}
                            onClick={() => sendToKassa(o)}
                          >
                            <Icon name="cash" size={16} /> На кассу
                          </button>
                        </>
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
                        className="btn sm block mt-2"
                        disabled={busy === o.id}
                        onClick={() => move(o, "in_progress")}
                      >
                        Взять в работу
                      </button>
                    )}
                    {col.key === "in_progress" && (
                      <button
                        className="btn sm block mt-2"
                        disabled={busy === o.id}
                        onClick={() => move(o, "ready")}
                      >
                        <Icon name="check" size={16} /> Готов
                      </button>
                    )}
                    {col.key === "ready" && o.paid_at && (
                      <button
                        className="btn sm block mt-2"
                        disabled={busy === o.id}
                        onClick={() => handOut(o, o.pay_method)}
                      >
                        <Icon name="share" size={16} /> Выдать
                      </button>
                    )}
                    {/* Касса: неоплаченный готовый заказ (заведён до подключения
                        кассы) оплачивается на ней же — ручных «наличными /
                        картой» при кассе нет. */}
                    {col.key === "ready" && !o.paid_at && kassa &&
                      (o.kassa_waiting ? (
                        <span className="badge pending mt-2">
                          <Icon name="cash" size={13} /> На кассе · №{o.id}
                        </span>
                      ) : (
                        <button
                          className="btn sm block mt-2"
                          disabled={busy === o.id}
                          onClick={() => sendToKassa(o)}
                        >
                          <Icon name="cash" size={16} /> На кассу
                        </button>
                      ))}
                    {col.key === "ready" && !o.paid_at && !kassa &&
                      (payFor === o.id ? (
                        <div className="stack tight mt-2">
                          <button
                            className="btn sm block"
                            disabled={busy === o.id}
                            onClick={() => handOut(o, "cash")}
                          >
                            <Icon name="cash" size={16} /> Наличными
                          </button>
                          <button
                            className="btn sm block"
                            disabled={busy === o.id}
                            onClick={() => handOut(o, "card")}
                          >
                            <Icon name="card" size={16} /> Картой
                          </button>
                        </div>
                      ) : (
                        <button className="btn sm block mt-2" onClick={() => setPayFor(o.id)}>
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
