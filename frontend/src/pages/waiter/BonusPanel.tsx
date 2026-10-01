import { useState } from "react";
import { post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import type { LoyaltyMember, Order } from "../../types";
import GuestEnroll from "./GuestEnroll";

type Found = Pick<LoyaltyMember, "name" | "phone" | "balance">;

/** Гость бонусной программы на заказе — у официанта и у баристы.

    Гость называет телефон — сотрудник находит его, и заказ закрепляется
    за ним: бонусы за чек начислятся, даже если гость ничего не списывает.
    Незарегистрированного можно записать тут же: если отправлять гостя
    регистрироваться самому, бонусы за этот чек он уже не получит.
    Списание — только когда владелец его разрешил (canRedeem) и деньги
    ещё не взяты.
*/
export default function BonusPanel({
  order,
  onDone,
  canRedeem,
}: {
  order: Order;
  onDone: () => Promise<void> | void;
  canRedeem: boolean;
}) {
  const notify = useToast();
  const [open, setOpen] = useState(false);
  const [phone, setPhone] = useState("");
  // гость уже указан на заказе — открываем сразу его, без повторного поиска
  const [member, setMember] = useState<Found | null>(null);
  const [busy, setBusy] = useState(false);
  const [signUp, setSignUp] = useState(false);

  const payable = Number(order.payable);
  // Бонусы уменьшают сумму к оплате, поэтому списывать можно только
  // до того, как деньги получены. Заказ, уже лежащий на кассе, сервер
  // снимет с неё и отправит заново с новой суммой.
  const redeemable =
    canRedeem && (order.status === "open" || order.status === "unpaid") && !order.paid_at;
  // списать можно не больше остатка счёта: сдачи с бонусов не бывает
  const canSwitch = Number(order.bonus_spent) <= 0 && !(order.paid_at && order.bonus_guest);
  // хотя бы 1 ₽ — деньгами: чек на 0 ₽ касса не пробьёт
  const maxRedeem =
    member && redeemable ? Math.max(0, Math.floor(Math.min(Number(member.balance), payable - 1))) : 0;

  function reset() {
    setOpen(false);
    setPhone("");
    setMember(null);
    setSignUp(false);
  }

  function openPanel() {
    setOpen(true);
    if (order.bonus_guest) {
      setMember(order.bonus_guest);
      setPhone(order.bonus_guest.phone);
    }
  }

  /** Найти гостя и закрепить за ним заказ. false — гостя нет в программе. */
  async function attach(): Promise<boolean> {
    const res = await post<{ member: LoyaltyMember; earned: string | number }>(
      `/orders/${order.id}/guest/`,
      { phone }
    );
    setMember(res.member);
    setPhone(res.member.phone);
    const earned = Number(res.earned);
    if (earned > 0) notify(`Начислено ${earned.toLocaleString("ru")} бонусов за заказ`, "ok");
    await onDone();
    return true;
  }

  async function find() {
    setBusy(true);
    setSignUp(false);
    try {
      await attach();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setSignUp(true); // гостя нет — предлагаем записать в программу
      } else {
        notify(e instanceof ApiError ? e.message : "Не удалось найти гостя", "bad");
      }
    } finally {
      setBusy(false);
    }
  }

  /** Гость записан — закрепляем за ним заказ. Запись уже прошла, поэтому
      форму убираем в любом случае: «Гостя нет в программе» было бы неправдой. */
  async function enrolled(m: LoyaltyMember) {
    setSignUp(false);
    try {
      await attach();
      notify(`${m.name} в программе · ${Number(m.balance).toLocaleString("ru")} бонусов`, "ok");
    } catch (e) {
      setPhone("");
      notify(
        `${m.name} записан в программу, но к этому заказу не привязан: ` +
          (e instanceof ApiError ? e.message : "ошибка связи"),
        "bad"
      );
    }
  }

  async function redeem(amount: number) {
    if (amount <= 0) return;
    setBusy(true);
    try {
      const res = await post<{ resent_to_kassa: boolean | null }>(
        `/orders/${order.id}/bonus/`,
        { phone, amount }
      );
      notify(`Списано ${amount.toLocaleString("ru")} бонусов`, "ok");
      if (res.resent_to_kassa === false) {
        notify("Касса не ответила — отправьте заказ на кассу ещё раз", "bad");
      }
      reset();
      await onDone();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось списать бонусы", "bad");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return order.bonus_guest ? (
      <button className="btn sm ghost block mt-2" onClick={openPanel}>
        <Icon name="gift" size={16} /> {order.bonus_guest.name} ·{" "}
        {Number(order.bonus_guest.balance).toLocaleString("ru")} б.
      </button>
    ) : (
      <button className="btn sm ghost block mt-2" onClick={openPanel}>
        <Icon name="gift" size={16} /> Бонусы гостя
      </button>
    );
  }

  return (
    <div className="rule-top mt-2">
      <div className="between mt-2">
        <span className="muted sm">Гость по телефону</span>
        <button className="icon-btn" onClick={reset} aria-label="Закрыть">
          <Icon name="close" size={16} />
        </button>
      </div>

      {!member && (
        <div className="wrap mt-2">
          <input
            className="input grow"
            value={phone}
            onChange={(e) => {
              setPhone(e.target.value);
              setSignUp(false);
            }}
            onKeyDown={(e) => e.key === "Enter" && phone.trim() && find()}
            placeholder="+7 999 000-00-00"
            inputMode="tel"
            autoFocus
          />
          <button className="btn sm" disabled={busy || !phone.trim()} onClick={find}>
            <Icon name="user" size={15} /> Найти
          </button>
        </div>
      )}

      {signUp && <GuestEnroll phone={phone} onEnrolled={enrolled} />}

      {member && (
        <div className="mt-2">
          <div className="between">
            <strong>{member.name}</strong>
            <span className="num">{Number(member.balance).toLocaleString("ru")} бонусов</span>
          </div>
          <div className="muted sm mt-1">
            {member.phone} ·{" "}
            {order.paid_at ? "бонусы за заказ начислены" : "бонусы начислятся при оплате"}
          </div>
          {redeemable && maxRedeem > 0 && (
            <div className="muted sm mt-2">
              Гость копит или тратит? Доступно {maxRedeem.toLocaleString("ru")} · 1 бонус = 1 ₽ · хотя бы 1 ₽ деньгами
              {order.kassa_waiting && " · заказ снимем с кассы и отправим с новой суммой"}
            </div>
          )}
          {/* Копить — выбор по умолчанию: гость уже закреплён за заказом,
              кнопка только подтверждает это и закрывает панель. */}
          <button
            className="btn block mt-2"
            disabled={busy}
            onClick={() => {
              notify(
                order.paid_at
                  ? `${member.name} копит — бонусы за заказ начислены`
                  : `${member.name} копит — бонусы начислятся при оплате`,
                "ok"
              );
              reset();
            }}
          >
            <Icon name="gift" size={16} /> Копить
          </button>
          {redeemable && maxRedeem > 0 && (
            <>
              <button
                className="btn ghost block mt-2"
                disabled={busy}
                onClick={() => redeem(maxRedeem)}
              >
                <Icon name="check" size={16} /> Списать {maxRedeem.toLocaleString("ru")}
              </button>
              {maxRedeem > 100 && (
                <button
                  className="btn sm ghost block mt-2"
                  disabled={busy}
                  onClick={() => redeem(Math.floor(maxRedeem / 2))}
                >
                  Списать половину · {Math.floor(maxRedeem / 2).toLocaleString("ru")}
                </button>
              )}
            </>
          )}
          {/* Сменить гостя нельзя, когда по заказу уже были его бонусы:
              списанное или начисленное осталось бы на другом человеке. */}
          {canSwitch && (
            <button
              className="btn sm ghost block mt-2"
              disabled={busy}
              onClick={() => {
                setMember(null);
                setPhone("");
              }}
            >
              Другой гость
            </button>
          )}
        </div>
      )}
    </div>
  );
}
