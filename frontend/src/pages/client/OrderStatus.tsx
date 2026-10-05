import { useState } from "react";
import { post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite } from "../../site";
import { minutesBetween } from "../../time";
import type { Order } from "../../types";
import GuestBonus from "./GuestBonus";

/** Последний телефон или почта для чека — подставим в следующий раз. */
const CONTACT_KEY = "receipt_contact";

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
  // «Оплатить на кассе» — заказ уходит на кассу заведения. Только на
  // стойке: в зале счёт на кассу отправляет официант.
  const canPayKassa = counter && site?.kassa_payment === true;
  const notify = useToast();
  const [paying, setPaying] = useState(false);
  // Банку нужен чек (54-ФЗ), а куда его прислать, мы не знаем: спрашиваем
  // телефон или почту. Прошлый ввод подставляем — гость платит не впервые.
  const [askContact, setAskContact] = useState(false);
  const [contact, setContact] = useState(() => {
    try {
      return localStorage.getItem(CONTACT_KEY) || "";
    } catch {
      return "";
    }
  });
  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const st = order.status;
  // Заказ ждёт гостя на кассе — экран только показывает номер.
  const atKassa = order.kassa_waiting && !order.paid_at && st !== "cancelled";
  // Сколько ещё ждёт оплаты неоплаченный заказ. Без этой строки гость не
  // понимает, что заказ живёт не вечно, и возвращается к отменённому.
  const leftToPay = Math.max(0, 15 - minutesBetween(order.created_at));
  const head =
    st === "requested" ? "Заявка принята"
    : st === "unpaid" ? "Ждём оплату"
    : st === "open" ? (order.is_ready ? "Готово!" : "Готовится")
    : st === "paid" ? "Заказ закрыт"
    : "Заказ отменён";
  const note =
    st === "requested" ? `Подойдите к стойке и назовите имя «${order.customer_name}» — официант оформит заказ.`
    : st === "unpaid"
      ? order.kassa_waiting
        ? `Подойдите к кассе и назовите номер ${order.id} — оплатить можно наличными или картой. Начнём готовить сразу после оплаты.`
        : `Оплатите заказ — и мы сразу начнём готовить. Номер для выдачи появится после оплаты.${
            leftToPay > 0 ? ` Заказ ждёт ещё ${leftToPay} мин.` : ""
          }`
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
        askContact ? { token, contact } : { token },
      );
      if (askContact) {
        try {
          localStorage.setItem(CONTACT_KEY, contact.trim());
        } catch {
          /* приватный режим — просто не запомним */
        }
      }
      window.location.href = payment_url;
    } catch (e) {
      if (e instanceof ApiError && e.body.need_contact) {
        // Не ошибка гостя, а вопрос: показываем поле, а не красный тост.
        if (askContact) notify(e.message, "bad");
        setAskContact(true);
      } else {
        notify(e instanceof ApiError ? e.message : "Не удалось начать оплату", "bad");
      }
      setPaying(false);
    }
  }

  /** Заказ уходит на кассу. Оплату касса подтвердит сама — опрос заказа
   *  покажет гостю номер выдачи, как только бариста пробьёт чек. */
  async function payAtKassa() {
    if (!token) return;
    setPaying(true);
    try {
      await post("/orders/pay_at_kassa/", { token });
      await onReload();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось отправить заказ на кассу", "bad");
    } finally {
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

      {/* Пока заказ ждёт на кассе, главное для гостя — какой номер назвать. */}
      {order.kassa_waiting && !order.paid_at && st !== "cancelled" && (
        <div className="pickup-no mt-4">
          <span className="muted">Номер на кассе</span>
          <strong>{order.id}</strong>
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
              <span className="row-body">
                <span>{it.product_name}</span>
                {it.options_text && (
                  <span className="muted sm">{it.options_text}</span>
                )}
              </span>
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

      {/* Гость выбрал «Оплатить на кассе» — выбор сделан, больше кнопок
          нет: ни другой оплаты, ни бонусов (сумма на кассе уже зафиксирована),
          ни отмены — ушедшего гостя отменяет бариста. Иначе гость, стоящий
          в очереди, не понимает, чего от него ещё хотят. */}
      {atKassa ? null : (
        <>
          <GuestBonus order={order} onDone={onReload} />

          {/* Оплаченный заказ кнопку не показывает, даже пока готовится: при
              предоплате деньги уже взяты, а статус ещё «открыт» — гость
              заплатил бы второй раз. «Онлайн», а не «картой»: на кассе картой
              тоже можно, и гость путался, чем эти две кнопки отличаются. */}
          {canPayOnline && askContact && !order.paid_at && st !== "paid" && st !== "cancelled" && (
            <label className="field mt-4">
              <span className="label">Куда прислать чек</span>
              <input
                className="input"
                autoComplete="email"
                autoCapitalize="none"
                placeholder="Телефон или почта"
                value={contact}
                onChange={(e) => setContact(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && contact.trim() && payOnline()}
                autoFocus
              />
            </label>
          )}
          {canPayOnline && !order.paid_at && st !== "paid" && st !== "cancelled" && (
            <button
              className={"btn block " + (askContact ? "mt-2" : "mt-4")}
              disabled={paying || (askContact && !contact.trim())}
              onClick={payOnline}
            >
              <Icon name="card" size={18} /> Оплатить онлайн ·{" "}
              {Number(order.payable).toLocaleString("ru")} ₽
            </button>
          )}
          {canPayKassa && !order.paid_at && (st === "unpaid" || st === "open") && (
            <button
              className={"btn block mt-3" + (canPayOnline ? " ghost" : "")}
              disabled={paying}
              onClick={payAtKassa}
            >
              <Icon name="cash" size={18} /> Оплатить на кассе
            </button>
          )}

          {st === "requested" || st === "unpaid" ? (
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
      )}
    </>
  );
}
