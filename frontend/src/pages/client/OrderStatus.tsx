import { useState } from "react";
import { post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite } from "../../site";
import type { Order } from "../../types";
import GuestBonus from "./GuestBonus";

/** Экран «Ваш заказ»: статус, номер выдачи, состав, оплата и отмена.

    Общий для обоих меню — обычного и ленты. Для гостя это главный экран
    после заказа: он смотрит сюда, пока ждёт, а на стойке ещё и забирает
    по номеру, так что разъехаться этим двум версиям нельзя.
*/
export default function OrderStatus({
  order,
  token,
  onReload,
  onForget,
}: {
  order: Order;
  token: string | null;
  /** Перечитать заказ (после списания бонусов — сразу, не ждать опроса). */
  onReload: () => Promise<void> | void;
  /** Забыть заказ: «Новый заказ» и успешная отмена. */
  onForget: () => void;
}) {
  const site = useSite();
  const counter = site?.service_mode === "counter";
  // кнопку оплаты показываем, только если банк подключён, — иначе гость
  // упрётся в ошибку провайдера
  const canPayOnline = site?.online_payment === true;
  const notify = useToast();
  const [paying, setPaying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const st = order.status;
  const head =
    st === "requested" ? "Заявка принята"
    : st === "open" ? (order.is_ready ? "Готово!" : "Готовится")
    : st === "paid" ? "Заказ закрыт"
    : "Заказ отменён";
  const note =
    st === "requested" ? `Подойдите к стойке и назовите имя «${order.customer_name}» — официант оформит заказ.`
    : st === "open"
      ? order.is_ready
        ? counter ? "Готово — подойдите к окну и назовите свой номер." : "Ваш заказ готов, можно забирать."
        : counter ? "Готовим. Следите за номером — здесь появится «готово»." : `Заказ готовится${order.table ? `, стол ${order.table}` : ""}.`
    : st === "paid" ? "Спасибо, что были у нас!"
    : "Заказ отменён.";

  /** Уводим гостя на страницу банка. Заказ пометит оплаченным вебхук,
   *  поэтому здесь ничего не меняем — вернувшись, гость увидит новый статус. */
  async function payOnline() {
    if (!token) return;
    setPaying(true);
    try {
      const { payment_url } = await post<{ payment_url: string }>(
        "/orders/pay_online/",
        { token },
      );
      window.location.href = payment_url;
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось начать оплату", "bad");
      setPaying(false);
    }
  }

  // гость отменяет свою заявку, пока официант её не подтвердил
  async function cancelRequest() {
    if (!token) return;
    setCancelling(true);
    try {
      await post("/orders/cancel_request/", { token });
      onForget();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось отменить", "bad");
    } finally {
      setCancelling(false);
      setConfirmCancel(false);
    }
  }

  return (
    <>
      {/* На стойке номер — единственный способ забрать заказ, поэтому крупно. */}
      {counter && order.daily_number != null && st !== "cancelled" && (
        <div className="pickup-no mt-4">
          <span className="muted">Ваш номер</span>
          <strong>{order.daily_number}</strong>
        </div>
      )}

      <div className="card enter mt-4">
        <div className="between">
          <strong className="title lg">{head}</strong>
          {st !== "requested" && (
            <span
              className={
                "badge " +
                (order.is_ready ? "ready" : st === "open" ? "preparing" : st === "paid" ? "paid" : "cancelled")
              }
            >
              {order.status_display}
            </span>
          )}
        </div>
        <p className="muted mt-2">{note}</p>
        <ul className="stack tight list mt-4">
          {order.items.map((it) => (
            <li key={it.id} className="between">
              <span>{it.product_name}</span>
              <span className="num muted">× {it.quantity}</span>
            </li>
          ))}
        </ul>
        <div className="between rule-top mt-3">
          <strong>Итого</strong>
          <strong className="num">{Number(order.total).toLocaleString("ru")} ₽</strong>
        </div>
        {Number(order.bonus_spent) > 0 && (
          <>
            <div className="between mt-2">
              <span className="muted">Бонусами</span>
              <span className="num">−{Number(order.bonus_spent).toLocaleString("ru")} ₽</span>
            </div>
            <div className="between mt-2">
              <strong>К оплате</strong>
              <strong className="num">{Number(order.payable).toLocaleString("ru")} ₽</strong>
            </div>
          </>
        )}
      </div>

      <GuestBonus order={order} onDone={onReload} />

      {canPayOnline && st !== "paid" && st !== "cancelled" && (
        <button className="btn block mt-4" disabled={paying} onClick={payOnline}>
          <Icon name="card" size={18} /> Оплатить картой ·{" "}
          {Number(order.payable).toLocaleString("ru")} ₽
        </button>
      )}

      {st === "requested" ? (
        confirmCancel ? (
          <div className="wrap mt-4" style={{ justifyContent: "center" }}>
            <span className="muted" style={{ alignSelf: "center" }}>Точно отменить заказ?</span>
            <button className="btn sm danger" disabled={cancelling} onClick={cancelRequest}>
              <Icon name="check" size={16} /> Да, отменить
            </button>
            <button className="btn sm ghost" onClick={() => setConfirmCancel(false)}>Нет</button>
          </div>
        ) : (
          <button className="btn ghost block mt-4" onClick={() => setConfirmCancel(true)}>
            <Icon name="minus" size={18} /> Отменить заказ
          </button>
        )
      ) : (
        <button className="btn ghost block mt-4" onClick={onForget}>
          <Icon name="plus" size={18} /> Новый заказ
        </button>
      )}
    </>
  );
}
