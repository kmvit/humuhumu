import { useEffect, useState } from "react";
import { get, patch, post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import Modal from "../../components/ui/Modal";
import { useToast } from "../../components/ui/Toast";
import { fmtDateTime } from "../../time";
import type { LoyaltyMember, MemberCard as Card } from "../../types";

const SOURCE: Record<string, string> = {
  guest: "записался сам",
  staff: "записал персонал",
  import: "перенесён из прежней системы",
};

// частые причины ручной корректировки — одним касанием
const REASONS = ["Подарок на день рождения", "Компенсация гостю", "Ошибка кассира"];

const n = (v: string | number) => Number(v).toLocaleString("ru", { maximumFractionDigits: 2 });

function date(iso: string): string {
  // «1988-11-02» — не через new Date(строка): та читается как полночь UTC
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("ru", { day: "numeric", month: "long", year: "numeric" });
  }
  return new Date(iso).toLocaleDateString("ru", { day: "numeric", month: "long", year: "numeric" });
}

/** Карточка гостя у владельца: данные, личный процент, ручная корректировка
    бонусов и вся история операций. */
export default function MemberCard({
  id,
  onClose,
  onChanged,
}: {
  id: number;
  onClose: () => void;
  /** гость изменился — обновить строку в списке */
  onChanged: (m: LoyaltyMember) => void;
}) {
  const notify = useToast();
  const [card, setCard] = useState<Card | null>(null);
  const [busy, setBusy] = useState(false);

  // правка
  const [name, setName] = useState("");
  const [birth, setBirth] = useState("");
  const [ownPercent, setOwnPercent] = useState(false);
  const [percent, setPercent] = useState("");

  // корректировка
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");

  const [allHistory, setAllHistory] = useState(false);

  function fill(c: Card) {
    setCard(c);
    setName(c.name);
    setBirth(c.birth_date ?? "");
    setOwnPercent(c.earn_percent != null);
    setPercent(c.earn_percent != null ? String(Number(c.earn_percent)) : "");
    onChanged(c);
  }

  useEffect(() => {
    get<Card>(`/loyalty/members/${id}/`)
      .then(fill)
      .catch((e) => {
        notify(e instanceof ApiError ? e.message : "Не удалось открыть гостя", "bad");
        onClose();
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (!card) {
    return (
      <Modal onClose={onClose}>
        <div className="skeleton sm" />
      </Modal>
    );
  }

  const programPercent = card.earn_percent == null ? Number(card.effective_percent) : null;
  const dirty =
    name.trim() !== card.name ||
    (birth || null) !== (card.birth_date || null) ||
    (ownPercent ? percent : null) !==
      (card.earn_percent != null ? String(Number(card.earn_percent)) : null);

  async function save() {
    if (!name.trim()) return notify("Имя не может быть пустым", "bad");
    if (ownPercent && (percent === "" || Number(percent) < 0 || Number(percent) > 50)) {
      return notify("Личный процент — от 0 до 50", "bad");
    }
    setBusy(true);
    try {
      const c = await patch<Card>(`/loyalty/members/${id}/`, {
        name: name.trim(),
        birth_date: birth || null,
        earn_percent: ownPercent ? percent : null,
      });
      fill(c);
      notify("Сохранено", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setBusy(false);
    }
  }

  async function adjust(sign: 1 | -1) {
    const v = Number(amount);
    if (!Number.isInteger(v) || v <= 0) return notify("Введите целое число бонусов", "bad");
    if (!reason.trim()) return notify("Напишите причину", "bad");
    setBusy(true);
    try {
      const c = await post<Card>(`/loyalty/members/${id}/adjust/`, {
        amount: sign * v,
        comment: reason.trim(),
      });
      fill(c);
      setAmount("");
      setReason("");
      notify(sign > 0 ? `Начислено ${n(v)} б.` : `Снято ${n(v)} б.`, "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось", "bad");
    } finally {
      setBusy(false);
    }
  }

  const history = allHistory ? card.transactions : card.transactions.slice(0, 15);

  return (
    <Modal
      onClose={onClose}
      head={
        <div className="between" style={{ alignItems: "flex-start" }}>
          <div style={{ minWidth: 0 }}>
            <strong className="title">{card.name}</strong>
            <div className="muted sm">{card.phone}</div>
          </div>
          <div className="wrap" style={{ alignItems: "center", flexWrap: "nowrap" }}>
            <strong className="num lg" style={{ whiteSpace: "nowrap" }}>{n(card.balance)} б.</strong>
            <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
              <Icon name="close" size={16} />
            </button>
          </div>
        </div>
      }
    >
      <p className="muted sm m-0">
        В программе с {date(card.created_at)}
        {card.source ? ` · ${SOURCE[card.source]}` : ""}
        {` · оплаченных заказов ${card.visits}`}
        {card.last_visit ? ` · последний ${date(card.last_visit)}` : ""}
        {` · начислено ${n(card.earned_total)} · потрачено ${n(card.spent_total)}`}
      </p>

      {/* ——— данные и процент ——— */}
      <div className="rule-top mt-3">
        <div className="wrap mt-3">
          <label className="field grow">
            <span className="label">Имя</span>
            <input className="input" value={name} maxLength={150} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span className="label">День рождения</span>
            <input className="input" type="date" value={birth} onChange={(e) => setBirth(e.target.value)} />
          </label>
        </div>

        <div className="mt-3">
          <span className="label">Начисление с покупки</span>
          <div className="wrap mt-1" style={{ alignItems: "center" }}>
            <button
              className={"btn sm" + (ownPercent ? " ghost" : "")}
              onClick={() => setOwnPercent(false)}
            >
              Общее{programPercent != null ? ` · ${n(programPercent)} %` : ""}
            </button>
            <button
              className={"btn sm" + (ownPercent ? "" : " ghost")}
              onClick={() => {
                setOwnPercent(true);
                if (percent === "") setPercent(String(Number(card.effective_percent)));
              }}
            >
              Личное
            </button>
            {ownPercent && (
              <>
                <input
                  className="input"
                  style={{ width: 90 }}
                  inputMode="decimal"
                  value={percent}
                  onChange={(e) => setPercent(e.target.value.replace(",", ".").replace(/[^\d.]/g, ""))}
                  aria-label="Личный процент"
                />
                <span className="muted sm">% с чека</span>
              </>
            )}
          </div>
          <p className="muted sm m-0 mt-1">
            Личный процент — постоянному гостю, другу заведения, сотруднику. Действует
            со следующего заказа.
          </p>
        </div>

        {dirty && (
          <button className="btn sm mt-3" onClick={save} disabled={busy}>
            <Icon name="check" size={15} /> Сохранить
          </button>
        )}
      </div>

      {/* ——— корректировка ——— */}
      <div className="rule-top mt-3">
        <strong className="mt-3" style={{ display: "block" }}>Начислить или снять бонусы</strong>
        <div className="wrap mt-2">
          <input
            className="input"
            style={{ width: 120 }}
            inputMode="numeric"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))}
            placeholder="Бонусов"
          />
          <input
            className="input grow"
            value={reason}
            maxLength={255}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Причина — увидите в истории"
          />
        </div>
        <div className="size-row">
          {REASONS.map((r) => (
            <button
              key={r}
              type="button"
              className={"size-chip" + (reason === r ? " active" : "")}
              onClick={() => setReason(r)}
            >
              {r}
            </button>
          ))}
        </div>
        <div className="wrap mt-2">
          <button className="btn sm" onClick={() => adjust(1)} disabled={busy}>
            <Icon name="plus" size={15} /> Начислить
          </button>
          <button className="btn sm ghost" onClick={() => adjust(-1)} disabled={busy}>
            <Icon name="minus" size={15} /> Снять
          </button>
        </div>
      </div>

      {/* ——— история ——— */}
      <div className="rule-top mt-3">
        <strong className="mt-3" style={{ display: "block" }}>История</strong>
        {card.transactions.length === 0 ? (
          <p className="muted sm">Операций пока нет.</p>
        ) : (
          <ul className="stack tight list mt-2">
            {history.map((t) => {
              const amt = Number(t.amount);
              return (
                <li key={t.id}>
                  <div className="between">
                    <span>{t.type_display}</span>
                    <strong
                      className="num"
                      style={{ color: amt < 0 ? "var(--danger)" : "var(--brand)", whiteSpace: "nowrap" }}
                    >
                      {amt > 0 ? "+" : "−"}
                      {n(Math.abs(amt))}
                    </strong>
                  </div>
                  <div className="muted sm">
                    {fmtDateTime(t.created_at)}
                    {t.comment ? ` · ${t.comment}` : ""}
                    {t.created_by_name ? ` · ${t.created_by_name}` : ""}
                    {` · остаток ${n(t.balance_after)}`}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {!allHistory && card.transactions.length > 15 && (
          <button className="btn sm ghost mt-2" onClick={() => setAllHistory(true)}>
            Показать всю историю ({card.transactions.length})
          </button>
        )}
      </div>
    </Modal>
  );
}
