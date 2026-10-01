import { useState } from "react";
import { get, ApiError } from "../../api";
import Icon from "../../components/Icon";
import Modal from "../../components/ui/Modal";
import { useToast } from "../../components/ui/Toast";
import type { LoyaltyMember } from "../../types";
import GuestEnroll from "./GuestEnroll";

export type GuestChoice = { phone: string; bonus: number } | null;

/** Оформление заказа на стойке: гость с бонусами — до отправки на кассу.

    На стойке с кассой заказ уходит на кассу сразу после создания, и сумма
    там — та, что была в эту секунду. Поэтому гостя и списание спрашиваем
    здесь, а не на карточке потом: иначе касса просила бы полную цену.
    Гость не называет телефон — сразу кнопка отправки, лишнего шага нет.
*/
export default function GuestCheckout({
  total,
  label,
  busy,
  canRedeem,
  onConfirm,
  onClose,
}: {
  total: number;
  /** Что произойдёт по кнопке: «На кассу», «Отправить». */
  label: string;
  busy: boolean;
  canRedeem: boolean;
  onConfirm: (choice: GuestChoice) => void;
  onClose: () => void;
}) {
  const notify = useToast();
  const [phone, setPhone] = useState("");
  const [member, setMember] = useState<LoyaltyMember | null>(null);
  const [signUp, setSignUp] = useState(false);
  const [finding, setFinding] = useState(false);
  const [spend, setSpend] = useState(0); // 0 — копит

  // хотя бы 1 ₽ — деньгами: чек на 0 ₽ касса не пробьёт
  const available =
    member && canRedeem ? Math.max(0, Math.floor(Math.min(Number(member.balance), total - 1))) : 0;
  const half = Math.floor(available / 2);
  const payable = total - spend;
  // Телефон набран, но гость не найден — не отправляем молча без него:
  // бариста думал бы, что бонусы учтены.
  const unresolved = !member && !signUp && phone.trim() !== "";

  async function find() {
    setFinding(true);
    setSignUp(false);
    setSpend(0);
    try {
      setMember(await get<LoyaltyMember>(`/loyalty/lookup/?phone=${encodeURIComponent(phone)}`));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setSignUp(true);
      else notify(e instanceof ApiError ? e.message : "Не удалось найти гостя", "bad");
    } finally {
      setFinding(false);
    }
  }

  const money = (v: number) => v.toLocaleString("ru");

  return (
    <Modal
      onClose={onClose}
      head={
        <div className="between">
          <strong className="title">Оплата заказа</strong>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" size={18} />
          </button>
        </div>
      }
    >
      <div className="stack">
        <div className="between">
          <span className="muted">Итого</span>
          <span className="num">{money(total)} ₽</span>
        </div>

        <div className="rule-top">
          <span className="muted sm">Гость с бонусами — по телефону</span>
          {!member ? (
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
              <button className="btn sm" disabled={finding || !phone.trim()} onClick={find}>
                <Icon name="user" size={15} /> Найти
              </button>
            </div>
          ) : (
            <div className="mt-2">
              <div className="between">
                <strong>{member.name}</strong>
                <span className="num">{money(Number(member.balance))} бонусов</span>
              </div>
              <div className="muted sm">{member.phone}</div>
              {/* Копить — выбор по умолчанию; списание — по желанию гостя. */}
              <div className="wrap mt-2">
                <button
                  className={"btn sm" + (spend === 0 ? "" : " ghost")}
                  onClick={() => setSpend(0)}
                >
                  <Icon name="gift" size={15} /> Копить
                </button>
                {available > 0 && (
                  <button
                    className={"btn sm" + (spend === available ? "" : " ghost")}
                    onClick={() => setSpend(available)}
                  >
                    Списать {money(available)}
                  </button>
                )}
                {half > 0 && half !== available && (
                  <button
                    className={"btn sm" + (spend === half ? "" : " ghost")}
                    onClick={() => setSpend(half)}
                  >
                    Половину · {money(half)}
                  </button>
                )}
                <button
                  className="btn sm ghost"
                  onClick={() => {
                    setMember(null);
                    setSpend(0);
                    setPhone("");
                  }}
                >
                  Другой гость
                </button>
              </div>
              {!canRedeem && (
                <p className="muted sm m-0 mt-1">Списание бонусов персоналом выключено</p>
              )}
            </div>
          )}
          {signUp && (
            <GuestEnroll
              phone={phone}
              onEnrolled={(m) => {
                setSignUp(false);
                setMember(m);
                notify(`${m.name} в программе · ${money(Number(m.balance))} бонусов`, "ok");
              }}
            />
          )}
        </div>

        <div className="rule-top between">
          <strong className="title">К оплате</strong>
          <strong className="title num">{money(payable)} ₽</strong>
        </div>
        {spend > 0 && (
          <div className="muted sm" style={{ marginTop: -8 }}>
            {money(spend)} бонусами
          </div>
        )}

        <button
          className="btn block"
          disabled={busy || signUp || unresolved}
          onClick={() => onConfirm(member ? { phone: member.phone, bonus: spend } : null)}
        >
          <Icon name={busy ? "spark" : "cash"} size={18} /> {label} · {money(payable)} ₽
        </button>
        {unresolved && (
          <p className="muted sm m-0 center">Нажмите «Найти» — или очистите телефон</p>
        )}
        {/* Гость передумал записываться или называть номер — отправляем
            без него, не заставляя стирать телефон. */}
        {(member || signUp || unresolved) && (
          <button className="btn ghost block" disabled={busy} onClick={() => onConfirm(null)}>
            Не указывать гостя
          </button>
        )}
      </div>
    </Modal>
  );
}
