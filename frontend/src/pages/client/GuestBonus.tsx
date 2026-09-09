import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get, post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite, useFeature } from "../../site";
import { useAuth } from "../../auth";
import type { LoyaltyMember, Order } from "../../types";

/** Бонусы гостя на экране его заказа: баланс и списание в счёт этого заказа.

    Показываем только когда владелец разрешил списывать самому гостю: во
    втором сценарии этим занимается официант на кассе, и кнопка тут только
    путала бы.
*/
export default function GuestBonus({
  order,
  onDone,
}: {
  order: Order;
  onDone: () => Promise<void> | void;
}) {
  const site = useSite();
  const { user } = useAuth();
  const notify = useToast();
  const [member, setMember] = useState<LoyaltyMember | null>(null);
  const [busy, setBusy] = useState(false);
  const on = useFeature("loyalty") && !!site?.bonus_enabled && !!site?.bonus_redeem_guest;

  useEffect(() => {
    if (!on || !user) return;
    get<LoyaltyMember>("/loyalty/me/")
      .then(setMember)
      .catch(() => setMember(null)); // не в программе — блок просто не покажем
  }, [on, user]);

  if (!on) return null;

  // Гость не вошёл — списывать нечем, но программу стоит показать:
  // иначе он не узнает, что бонусы вообще есть.
  if (!user) {
    return (
      <div className="card mt-4">
        <div className="between">
          <div>
            <strong className="title">Бонусы</strong>
            <p className="muted subtitle m-0">
              1 бонус = 1 ₽ · при регистрации{" "}
              {(site?.bonus_welcome ?? 0).toLocaleString("ru")} приветственных
            </p>
          </div>
          <Link className="btn sm" to="/login">
            <Icon name="gift" size={15} /> Войти
          </Link>
        </div>
      </div>
    );
  }

  if (!member) return null;

  const payable = Number(order.payable);
  const spent = Number(order.bonus_spent);
  // сдачи с бонусов не бывает — списываем не больше остатка счёта
  const maxRedeem = Math.min(Number(member.balance), payable);
  const canRedeem = order.status === "open" || order.status === "requested";

  async function redeem(amount: number) {
    if (amount <= 0) return;
    setBusy(true);
    try {
      await post(`/orders/${order.id}/bonus/`, { amount });
      notify(`Списано ${amount.toLocaleString("ru")} бонусов`, "ok");
      const m = await get<LoyaltyMember>("/loyalty/me/").catch(() => null);
      if (m) setMember(m);
      await onDone();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось списать бонусы", "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card mt-4">
      <div className="between">
        <strong className="title">Бонусы</strong>
        <span className="num">{Number(member.balance).toLocaleString("ru")}</span>
      </div>

      {spent > 0 && (
        <div className="between mt-2">
          <span className="muted sm">Списано за этот заказ</span>
          <span className="num">−{spent.toLocaleString("ru")} ₽</span>
        </div>
      )}

      {!canRedeem ? (
        <p className="muted sm mt-2 m-0">
          {spent > 0 ? "Бонусы учтены в этом заказе." : "Заказ уже закрыт."}
        </p>
      ) : maxRedeem <= 0 ? (
        <p className="muted sm mt-2 m-0">
          {Number(member.balance) <= 0
            ? "Копятся с каждой покупки — 1 бонус = 1 ₽."
            : "Весь заказ уже оплачен бонусами."}
        </p>
      ) : (
        <>
          <p className="muted sm mt-2 m-0">
            Можно списать {maxRedeem.toLocaleString("ru")} из{" "}
            {payable.toLocaleString("ru")} ₽ · 1 бонус = 1 ₽
          </p>
          <button className="btn block mt-3" disabled={busy} onClick={() => redeem(maxRedeem)}>
            <Icon name="gift" size={17} /> Списать {maxRedeem.toLocaleString("ru")}
            {maxRedeem >= payable ? " — заказ закрыт" : ""}
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
    </div>
  );
}
