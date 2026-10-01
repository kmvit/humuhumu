import { useEffect, useState, type ReactNode } from "react";
import Icon from "../../components/Icon";
import Modal from "../../components/ui/Modal";
import type { Order } from "../../types";

const money = (v: string | number) => Number(v).toLocaleString("ru", { maximumFractionDigits: 2 });

/** Экран оплаты на стойке с кассой: заказ сначала оплачивают, потом готовят.

    Открывается сразу, как бариста отправил заказ «На оплату», — чтобы не
    искать его потом в колонке «Ждут оплаты». Ничего не оплачивает сам:
    оплату пробивают на кассе, а сюда она приходит опросом доски. Как
    только касса подтвердила, экран показывает номер выдачи и закрывается.

    Заказ берём из списка доски по id, а не храним копию: иначе экран
    показывал бы «ждём оплату», когда доска уже знает, что заплатили.
*/
export default function PayAtKassa({
  order,
  busy,
  onClose,
  onResend,
  onCancelOrder,
  bonus,
  paidFallback,
}: {
  /** null — заказа на доске больше нет (отменили, истёк). */
  order: Order | null;
  /** Панель бонусов: гость вспомнил про них у окна — сумму пересчитают. */
  bonus?: ReactNode;
  /** «Оплачено» — если касса сама не подтвердила оплату. */
  paidFallback?: ReactNode;
  busy: boolean;
  onClose: () => void;
  onResend: (order: Order) => void;
  onCancelOrder: (order: Order) => void;
}) {
  const [confirmCancel, setConfirmCancel] = useState(false);
  const paid = !!order && order.status !== "unpaid" && !!order.paid_at;

  // Оплачено — показываем номер выдачи и уходим сами: бариста уже у
  // кофемашины, нажимать «закрыть» ему некогда.
  useEffect(() => {
    if (!paid) return;
    const t = window.setTimeout(onClose, 2500);
    return () => window.clearTimeout(t);
  }, [paid, onClose]);

  // Заказ пропал с доски — его отменили; экран больше не о чем.
  useEffect(() => {
    if (!order) onClose();
  }, [order, onClose]);

  if (!order) return null;

  return (
    <Modal onClose={onClose}>
      {paid ? (
        <div className="stack center">
          <span className="badge paid" style={{ alignSelf: "center" }}>
            <Icon name="check" size={14} /> Оплачено
            {order.pay_method_display ? ` · ${order.pay_method_display.toLowerCase()}` : ""}
          </span>
          <div className="pickup-no">
            <span className="muted">Номер выдачи</span>
            <strong>{order.daily_number ?? order.id}</strong>
          </div>
          <p className="muted m-0">Заказ ушёл в «Новые» — можно готовить.</p>
          <button className="btn block" onClick={onClose}>
            <Icon name="check" size={18} /> Готово
          </button>
        </div>
      ) : (
        <div className="stack">
          <div className="pickup-no">
            <span className="muted">{order.kassa_waiting ? "Пробейте на кассе заказ" : "Заказ"}</span>
            <strong>№{order.id}</strong>
          </div>

          <div className="between">
            <strong className="title">К оплате</strong>
            <strong className="title num">{money(order.payable)} ₽</strong>
          </div>
          <ul className="stack tight list m-0">
            {order.items.map((it) => (
              <li key={it.id} className="between">
                <span>
                  {it.product_name}
                  {it.options_text && <span className="muted sm"> · {it.options_text}</span>}
                </span>
                <span className="num muted">× {it.quantity}</span>
              </li>
            ))}
          </ul>

          {bonus}

          {order.kassa_waiting ? (
            <>
              <p className="muted m-0">
                <Icon name="spark" size={14} /> Ждём оплату на кассе — наличными или картой. Как
                только касса пробьёт чек, заказ сам уйдёт в работу.
              </p>
              {paidFallback}
            </>
          ) : (
            <>
              <p className="muted m-0">
                Заказ не дошёл до кассы — касса не ответила. Проверьте, что она включена и в
                сети, и отправьте ещё раз.
              </p>
              <button className="btn block" disabled={busy} onClick={() => onResend(order)}>
                <Icon name="cash" size={18} /> Отправить на кассу ещё раз
              </button>
            </>
          )}

          {confirmCancel ? (
            <div className="wrap" style={{ justifyContent: "center" }}>
              <span className="muted" style={{ alignSelf: "center" }}>
                Отменить заказ и снять с кассы?
              </span>
              <button className="btn sm danger" disabled={busy} onClick={() => onCancelOrder(order)}>
                Да, отменить
              </button>
              <button className="btn sm ghost" onClick={() => setConfirmCancel(false)}>Нет</button>
            </div>
          ) : (
            <div className="grid cols-2">
              {/* Гость ушёл за картой — заказ ждёт в «Ждут оплаты», экран
                  можно открыть снова с карточки. */}
              <button className="btn ghost" onClick={onClose}>Свернуть</button>
              <button className="btn ghost" onClick={() => setConfirmCancel(true)}>
                Отменить заказ
              </button>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
