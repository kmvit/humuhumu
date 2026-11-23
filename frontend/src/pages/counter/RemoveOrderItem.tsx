import { useId, useRef, useState } from "react";
import { ApiError, post } from "../../api";
import type { Order, OrderItem } from "../../types";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";

export default function RemoveOrderItem({ order, item, onDone }: {
  order: Order; item: OrderItem; onDone: (order: Order) => void;
}) {
  const toast = useToast();
  const panelId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);

  async function remove() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const updated = await post<Order>(`/orders/${order.id}/remove_item/`, {
        item_id: item.id,
      });
      onDone(updated);
      const messages = [updated.status === "cancelled" ? "Последняя позиция и заказ отменены" : "Позиция отменена, сумма пересчитана"];
      if (Number(order.bonus_spent) > 0) messages.push("Бонусы возвращены гостю");
      if (order.kassa_waiting && updated.status !== "cancelled" && !updated.kassa_waiting) {
        messages.push("Отправьте обновлённый заказ на кассу кнопкой «На кассу»");
      }
      toast(messages.join(". "));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось отменить позицию");
    } finally {
      setBusy(false);
    }
  }

  function dismiss() {
    setConfirm(false);
    setError("");
    trigger.current?.focus();
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={"order-item-remove" + (confirm ? " is-open" : "")}
        title={`Отменить позицию «${item.product_name}»`}
        aria-label={`Отменить позицию «${item.product_name}»`}
        aria-expanded={confirm}
        aria-controls={confirm ? panelId : undefined}
        disabled={busy}
        onClick={() => confirm ? dismiss() : setConfirm(true)}
      >
        <Icon name="close" size={17} />
      </button>
      {confirm && (
        <div className="order-item-confirm" id={panelId} onKeyDown={(e) => {
          if (e.key === "Escape" && !busy) { e.stopPropagation(); dismiss(); }
        }}>
          <div className="order-item-confirm-title">Отменить позицию?</div>
          <p className="order-item-confirm-detail">
            {item.product_name} × {item.quantity}
            {order.items.length === 1 && " · заказ будет отменён"}
          </p>
          {error && <p className="order-item-confirm-error" role="alert">{error}</p>}
          <div className="order-item-confirm-actions">
            <button type="button" className="order-item-keep" disabled={busy} onClick={dismiss} autoFocus>Оставить</button>
            <button type="button" className="order-item-delete" disabled={busy} onClick={remove}>
              {busy ? "Отменяем…" : "Отменить"}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
