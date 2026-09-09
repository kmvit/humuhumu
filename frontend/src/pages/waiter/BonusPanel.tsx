import { useState } from "react";
import { get, post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite } from "../../site";
import type { LoyaltyMember, Order } from "../../types";

/** Списание бонусов официантом на закрытии счёта.

    Гость называет телефон — официант находит его, видит баланс и списывает.
    Незарегистрированного можно записать в программу тут же: если отправлять
    гостя регистрироваться самому, бонусы за этот чек он уже не получит.
*/
export default function BonusPanel({
  order,
  onDone,
}: {
  order: Order;
  onDone: () => Promise<void> | void;
}) {
  const site = useSite();
  const notify = useToast();
  const [open, setOpen] = useState(false);
  const [phone, setPhone] = useState("");
  const [member, setMember] = useState<LoyaltyMember | null>(null);
  const [busy, setBusy] = useState(false);
  const [signUp, setSignUp] = useState(false);
  const [name, setName] = useState("");
  const [birth, setBirth] = useState("");

  const payable = Number(order.payable);
  // списать можно не больше остатка счёта: сдачи с бонусов не бывает
  const maxRedeem = member ? Math.min(Number(member.balance), payable) : 0;

  function reset() {
    setOpen(false);
    setPhone("");
    setMember(null);
    setSignUp(false);
    setName("");
    setBirth("");
  }

  async function find() {
    setBusy(true);
    setSignUp(false);
    try {
      const m = await get<LoyaltyMember>(`/loyalty/lookup/?phone=${encodeURIComponent(phone)}`);
      setMember(m);
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

  async function enroll() {
    if (!name.trim()) {
      notify("Укажите имя гостя", "bad");
      return;
    }
    setBusy(true);
    try {
      const m = await post<LoyaltyMember>("/loyalty/enroll/", {
        name: name.trim(),
        phone,
        ...(birth ? { birth_date: birth } : {}),
      });
      setMember(m);
      setSignUp(false);
      notify(`Гость в программе · ${Number(m.balance).toLocaleString("ru")} бонусов`, "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось зарегистрировать", "bad");
    } finally {
      setBusy(false);
    }
  }

  async function redeem(amount: number) {
    if (amount <= 0) return;
    setBusy(true);
    try {
      await post(`/orders/${order.id}/bonus/`, { phone, amount });
      notify(`Списано ${amount.toLocaleString("ru")} бонусов`, "ok");
      reset();
      await onDone();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось списать бонусы", "bad");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="btn sm ghost block mt-2" onClick={() => setOpen(true)}>
        <Icon name="gift" size={16} /> Бонусы гостя
      </button>
    );
  }

  return (
    <div className="rule-top mt-2">
      <div className="between mt-2">
        <span className="muted sm">Бонусы по телефону гостя</span>
        <button className="icon-btn" onClick={reset} aria-label="Закрыть">
          <Icon name="close" size={16} />
        </button>
      </div>

      <div className="wrap mt-2">
        <input
          className="input grow"
          value={phone}
          onChange={(e) => {
            setPhone(e.target.value);
            setMember(null);
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

      {signUp && (
        <div className="mt-2">
          <div className="muted sm">
            Гостя нет в программе. Записать — сразу{" "}
            {(site?.bonus_welcome ?? 200).toLocaleString("ru")} приветственных бонусов.
          </div>
          <div className="wrap mt-2">
            <input
              className="input grow"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Имя гостя"
              maxLength={150}
            />
            <input
              className="input"
              style={{ width: 150 }}
              type="date"
              value={birth}
              onChange={(e) => setBirth(e.target.value)}
              aria-label="Дата рождения"
            />
          </div>
          <button className="btn sm block mt-2" disabled={busy} onClick={enroll}>
            <Icon name="gift" size={15} /> Записать в программу
          </button>
        </div>
      )}

      {member && (
        <div className="mt-2">
          <div className="between">
            <strong>{member.name}</strong>
            <span className="num">{Number(member.balance).toLocaleString("ru")} бонусов</span>
          </div>
          {maxRedeem <= 0 ? (
            <div className="muted sm mt-1">Списывать нечего — бонусов нет.</div>
          ) : (
            <>
              <div className="muted sm mt-1">
                К списанию доступно {maxRedeem.toLocaleString("ru")} · 1 бонус = 1 ₽
              </div>
              <button
                className="btn block mt-2"
                disabled={busy}
                onClick={() => redeem(maxRedeem)}
              >
                <Icon name="check" size={16} /> Списать {maxRedeem.toLocaleString("ru")}
                {maxRedeem >= payable ? " — счёт закрыт бонусами" : ""}
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
      )}
    </div>
  );
}
