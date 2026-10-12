import type { ReactNode } from "react";
import Icon from "../../components/Icon";

/** Журналы склада — приходы и списания — по месяцам, а внутри по дням.

    Лентой за всё время они не листаются: за пару месяцев работы это
    сотни карточек. Месяц выбирается стрелками, дни свёрнуты в шапки с
    итогом, раскрыт только последний. */

/** «2026-10» для запроса. */
export function monthKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function monthTitle(key: string): string {
  const [y, m] = key.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("ru", { month: "long", year: "numeric" });
}

export function shiftMonth(key: string, delta: number): string {
  const [y, m] = key.split("-").map(Number);
  return monthKey(new Date(y, m - 1 + delta, 1));
}

/** 1 приход, 2 прихода, 5 приходов. */
export function plural(n: number, forms: [string, string, string]): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  const form =
    mod10 === 1 && mod100 !== 11
      ? forms[0]
      : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
        ? forms[1]
        : forms[2];
  return `${n} ${form}`;
}

/** День записи по местному времени — «2026-10-05». */
function dayKey(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** «Сегодня», «Вчера», «5 октября, вс». */
export function dayTitle(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  const day = new Date(y, m - 1, d);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - day.getTime()) / 86400000);
  if (diff === 0) return "Сегодня";
  if (diff === 1) return "Вчера";
  const date = day.toLocaleDateString("ru", { day: "numeric", month: "long" });
  return `${date}, ${day.toLocaleDateString("ru", { weekday: "short" })}`;
}

/** Записи по дням, новые дни сверху; порядок внутри дня сохраняется. */
export function groupByDay<T extends { created_at: string }>(rows: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const r of rows) {
    const k = dayKey(r.created_at);
    const list = groups.get(k);
    if (list) list.push(r);
    else groups.set(k, [r]);
  }
  return [...groups.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1));
}

export function MonthNav({
  month,
  onChange,
  children,
}: {
  month: string;
  onChange: (m: string) => void;
  /** итог месяца под стрелками */
  children?: ReactNode;
}) {
  const thisMonth = monthKey(new Date());
  return (
    <div className="card">
      <div className="cal-head">
        <button
          className="icon-btn"
          aria-label="Предыдущий месяц"
          onClick={() => onChange(shiftMonth(month, -1))}
        >
          <Icon name="chevronLeft" size={16} />
        </button>
        <span className="cal-month">{monthTitle(month)}</span>
        <button
          className="icon-btn"
          aria-label="Следующий месяц"
          disabled={month >= thisMonth}
          onClick={() => onChange(shiftMonth(month, 1))}
        >
          <Icon name="chevronRight" size={16} />
        </button>
      </div>
      {children}
    </div>
  );
}

/** Шапка дня: дата, сколько записей и на какую сумму. Касание — раскрыть. */
export function DayGroup({
  day,
  meta,
  open,
  onToggle,
  children,
}: {
  day: string;
  meta: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className="day-group">
      <button className="day-head" onClick={onToggle} aria-expanded={open}>
        <span className="day-title">
          <Icon name={open ? "chevronDown" : "chevronRight"} size={16} /> {dayTitle(day)}
        </span>
        <span className="muted sm num">{meta}</span>
      </button>
      {open && <div className="stack loose">{children}</div>}
    </section>
  );
}
