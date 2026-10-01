import { useState, type ReactNode } from "react";
import { post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import type { Order } from "../../types";

/** «Оплачено» — подстраховка, если касса сама не подтвердила оплату.

    Сначала сервер спрашивает кассу: подтвердит — заказ уходит в работу
    обычным путём. Не подтвердит (не ответила, назвала оплату иначе) —
    бариста отвечает, пробит ли чек и чем заплатили, и заказ идёт в работу
    с отметкой «вручную». Владелец потом сверяет такие оплаты с кассой.
    Подтверждение в карточке, а не confirm(): в киоск-браузере он глушится.
*/
export default function KassaPaid({
  order,
  onDone,
  beside,
}: {
  order: Order;
  onDone: () => Promise<void> | void;
  /** Кнопка в ту же строку справа (на карточке — «Отменить»). */
  beside?: ReactNode;
}) {
  const notify = useToast();
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);

  async function send(method?: "cash" | "card") {
    setBusy(true);
    try {
      const res = await post<{ confirmed: "kassa" | "manual" }>(
        `/orders/${order.id}/kassa_paid/`,
        method ? { method } : {}
      );
      notify(
        res.confirmed === "kassa"
          ? "Касса подтвердила оплату — заказ в работе"
          : "Оплата отмечена вручную — заказ в работе",
        "ok"
      );
      setAsk(false);
      await onDone();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setAsk(true); // касса молчит — спрашиваем баристу
      } else {
        notify(e instanceof ApiError ? e.message : "Не удалось отметить оплату", "bad");
      }
    } finally {
      setBusy(false);
    }
  }

  if (!ask) {
    const paid = (
      <button className="btn sm success block" disabled={busy} onClick={() => send()}>
        <Icon name="check" size={15} /> {busy ? "Спрашиваем…" : "Оплачено"}
      </button>
    );
    return beside ? (
      <div className="btn-row mt-2">
        {paid}
        {beside}
      </div>
    ) : (
      <div className="mt-2">{paid}</div>
    );
  }

  return (
    <div className="rule-top mt-2">
      <p className="sm m-0">
        Касса оплату не подтвердила. <strong>Чек на кассе пробит?</strong> Чем заплатили:
      </p>
      <div className="stack tight mt-2">
        <button className="btn sm block" disabled={busy} onClick={() => send("cash")}>
          <Icon name="cash" size={15} /> Наличными
        </button>
        <button className="btn sm block" disabled={busy} onClick={() => send("card")}>
          <Icon name="card" size={15} /> Картой
        </button>
        <button className="btn sm ghost block" disabled={busy} onClick={() => setAsk(false)}>
          Чек не пробит — отмена
        </button>
      </div>
    </div>
  );
}
