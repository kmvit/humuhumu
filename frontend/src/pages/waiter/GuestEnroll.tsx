import { useState } from "react";
import { post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { useSite } from "../../site";
import type { LoyaltyMember } from "../../types";

/** Записать гостя в бонусную программу с рабочего экрана.

    Общая для карточки заказа и оформления нового: гостя по номеру нет —
    сотрудник записывает его тут же, с согласием на обработку данных.
*/
export default function GuestEnroll({
  phone,
  onEnrolled,
}: {
  phone: string;
  onEnrolled: (member: LoyaltyMember) => Promise<void> | void;
}) {
  const site = useSite();
  const notify = useToast();
  const [name, setName] = useState("");
  const [birth, setBirth] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function enroll() {
    if (!name.trim()) {
      notify("Укажите имя гостя", "bad");
      return;
    }
    setBusy(true);
    let member: LoyaltyMember;
    try {
      member = await post<LoyaltyMember>("/loyalty/enroll/", {
        name: name.trim(),
        phone,
        consent: true,
        ...(birth ? { birth_date: birth } : {}),
      });
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось зарегистрировать", "bad");
      setBusy(false);
      return;
    }
    try {
      await onEnrolled(member);
    } finally {
      setBusy(false);
    }
  }

  return (
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
      <label className="inline tight mt-2">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span className="sm">Гость согласен на обработку персональных данных</span>
      </label>
      <button className="btn sm block mt-2" disabled={busy || !consent} onClick={enroll}>
        <Icon name="gift" size={15} /> Записать в программу
      </button>
    </div>
  );
}
