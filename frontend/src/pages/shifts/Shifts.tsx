import { useCallback, useEffect, useMemo, useState } from "react";
import { get, post, ApiError } from "../../api";
import type { Payroll, Shift, StaffUser } from "../../types";
import Icon, { type IconName } from "../../components/Icon";
import { useAuth } from "../../auth";
import { useToast } from "../../components/ui/Toast";

// «2000.00» → «2 000», «1234.50» → «1 234,5»
function fmtMoney(v: string | number | null | undefined): string {
  return Number(v ?? 0).toLocaleString("ru", { maximumFractionDigits: 2 });
}

function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString("ru", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
  });
}

// местная дата в «ГГГГ-ММ-ДД» (toISOString сдвинул бы день по UTC)
function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

const DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

// 1 смена, 2 смены, 5 смен
function fmtDays(n: number): string {
  const ten = n % 100;
  const one = n % 10;
  if (ten >= 11 && ten <= 14) return `${n} смен`;
  if (one === 1) return `${n} смена`;
  if (one >= 2 && one <= 4) return `${n} смены`;
  return `${n} смен`;
}

export default function Shifts() {
  const { user } = useAuth();
  // менеджер («Склад») и админ считают зарплату — видят смены и выплаты всей команды
  const isManager = user?.role === "admin" || user?.role === "warehouse";

  const today = useMemo(() => isoDay(new Date()), []);
  const [tab, setTab] = useState<"day" | "history" | "payroll">("day");
  const [day, setDay] = useState(today);
  const [month, setMonth] = useState(() => today.slice(0, 7));
  const [monthDays, setMonthDays] = useState<Shift[]>([]);
  const [shift, setShift] = useState<Shift | null>(null);
  const [staff, setStaff] = useState<StaffUser[]>([]);
  const [history, setHistory] = useState<Shift[]>([]);
  const [payroll, setPayroll] = useState<Payroll | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [penEdit, setPenEdit] = useState(false); // менеджер правит ручной штраф
  const [penVal, setPenVal] = useState("");
  const notify = useToast();

  // период для истории и сводки — по умолчанию последние 30 дней
  const [from, setFrom] = useState(() =>
    isoDay(new Date(Date.now() - 30 * 86400000))
  );
  const [to, setTo] = useState(() => isoDay(new Date()));

  const loadDay = useCallback(async (d: string) => {
    setShift(await get<Shift>(`/shifts/day/?date=${d}`));
  }, []);

  const loadMonth = useCallback(async (m: string) => {
    const res = await get<{ days: Shift[] }>(`/shifts/month/?month=${m}`);
    setMonthDays(res.days);
  }, []);

  const loadPeriod = useCallback(async () => {
    const q = `?from=${from}&to=${to}`;
    const [h, p] = await Promise.all([
      get<Shift[]>(`/shifts/${q}`),
      get<Payroll>(`/shifts/payroll/${q}`),
    ]);
    setHistory(h);
    setPayroll(p);
  }, [from, to]);

  useEffect(() => {
    setPenEdit(false); // при смене дня закрываем правку штрафа
    loadDay(day)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [loadDay, day]);

  useEffect(() => {
    loadMonth(month).catch(() => {});
  }, [loadMonth, month]);

  useEffect(() => {
    loadPeriod().catch(() => {});
  }, [loadPeriod]);

  // сетка месяца: пустые клетки до первого дня + сами дни
  const cells = useMemo(() => {
    const [y, m] = month.split("-").map(Number);
    const shifted = (new Date(y, m - 1, 1).getDay() + 6) % 7; // неделя с понедельника
    const total = new Date(y, m, 0).getDate();
    const byDate = new Map(monthDays.map((s) => [s.date, s]));
    return [
      ...Array.from({ length: shifted }, () => null),
      ...Array.from({ length: total }, (_, i) => {
        const date = isoDay(new Date(y, m - 1, i + 1));
        return { date, num: i + 1, info: byDate.get(date) ?? null };
      }),
    ];
  }, [month, monthDays]);

  function shiftMonth(delta: number) {
    const [y, m] = month.split("-").map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    setMonth(isoDay(d).slice(0, 7));
  }

  // состав смены правит только менеджер (склад) или админ
  const canEdit = shift?.can_edit ?? false;
  useEffect(() => {
    if (!canEdit || staff.length) return;
    get<StaffUser[]>("/shifts/staff/").then(setStaff).catch(() => {});
  }, [canEdit, staff.length]);

  const free = useMemo(
    () => staff.filter((s) => !shift?.members.some((m) => m.user === s.id)),
    [staff, shift]
  );

  async function changeMember(userId: number, add: boolean) {
    setBusy(true);
    try {
      setShift(
        await post<Shift>(`/shifts/${add ? "add_member" : "remove_member"}/`, {
          user: userId,
          date: day,
        })
      );
      setAdding(false);
      notify(add ? "Поставлен в смену" : "Убран из смены", "ok");
      loadMonth(month).catch(() => {});
      loadPeriod().catch(() => {});
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не получилось", "bad");
    } finally {
      setBusy(false);
    }
  }

  async function saveShiftPenalty() {
    setBusy(true);
    try {
      setShift(
        await post<Shift>("/shifts/set_penalty/", {
          date: day,
          penalty: Number(penVal || 0),
        })
      );
      setPenEdit(false);
      notify("Штраф за смену сохранён", "ok");
      loadPeriod().catch(() => {});
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не получилось", "bad");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted">Загрузка…</p>;

  // Выручку дня видит только менеджер/админ — линейному персоналу оставляем
  // бонус и его выплату, но не общую выручку заведения.
  const stats: { icon: IconName; label: string; value: string }[] | null = shift
    ? [
        ...(isManager
          ? [{ icon: "chart" as IconName, label: "Выручка за день", value: fmtMoney(shift.revenue) }]
          : []),
        {
          icon: "spark",
          label: `Бонус ${fmtMoney(shift.bonus_percent)}% на всех`,
          value: fmtMoney(shift.bonus_pool),
        },
        { icon: "gift", label: "Списания (подарки)", value: fmtMoney(shift.penalty) },
        { icon: "wallet", label: "К выплате на человека", value: fmtMoney(shift.payout) },
      ]
    : null;

  return (
    <>
      <h1 className="h1">Смены</h1>

      <div className="tabs">
        <button
          className={"navlink" + (tab === "day" ? " active" : "")}
          onClick={() => setTab("day")}
        >
          <Icon name="store" size={16} /> Смена
        </button>
        <button
          className={"navlink" + (tab === "history" ? " active" : "")}
          onClick={() => setTab("history")}
        >
          <Icon name="receipt" size={16} /> {isManager ? "Все смены" : "Мои смены"}
        </button>
        <button
          className={"navlink" + (tab === "payroll" ? " active" : "")}
          onClick={() => setTab("payroll")}
        >
          <Icon name="wallet" size={16} /> К выплате
        </button>
      </div>

      {/* ——— смена на день ——— */}
      {tab === "day" && shift && (
        <>
          {/* календарь месяца: подсвечены дни с поставленной сменой */}
          <div className="card mt-3">
            <div className="cal-head">
              <button
                className="icon-btn"
                aria-label="Предыдущий месяц"
                onClick={() => shiftMonth(-1)}
              >
                <Icon name="chevronLeft" size={16} />
              </button>
              <span className="cal-month">
                {new Date(month + "-01").toLocaleDateString("ru", {
                  month: "long",
                  year: "numeric",
                })}
              </span>
              <button
                className="icon-btn"
                aria-label="Следующий месяц"
                onClick={() => shiftMonth(1)}
              >
                <Icon name="chevronRight" size={16} />
              </button>
            </div>
            <div className="cal-grid">
              {DOW.map((d) => (
                <div className="cal-dow" key={d}>
                  {d}
                </div>
              ))}
              {cells.map((c, i) =>
                c === null ? (
                  <span className="cal-day blank" key={`blank-${i}`} />
                ) : (
                  <button
                    key={c.date}
                    className={
                      "cal-day" +
                      (c.info ? " filled" : "") +
                      (c.info?.mine ? " mine" : "") +
                      (c.date === today ? " today" : "") +
                      (c.date === day ? " selected" : "")
                    }
                    title={
                      c.info
                        ? `${c.info.members_count} чел · выручка ${fmtMoney(c.info.revenue)}`
                        : "Смена не поставлена"
                    }
                    onClick={() => setDay(c.date)}
                  >
                    <span className="cal-num">{c.num}</span>
                    {c.info && <span className="cal-tag">{c.info.members_count}</span>}
                  </button>
                )
              )}
            </div>
          </div>

          <div className="card hover mt-4">
            <div className="between">
              <div className="stack tight">
                <strong className="title">
                  {fmtDay(shift.date)}
                </strong>
                <span className="muted">
                  {shift.members_count === 0
                    ? "Смена не поставлена"
                    : shift.in_shift
                      ? "Вы в этой смене"
                      : `В смене ${shift.members_count} чел.`}
                </span>
              </div>
              {day !== today && (
                <button
                  className="btn ghost sm"
                  onClick={() => {
                    setDay(today);
                    setMonth(today.slice(0, 7));
                  }}
                >
                  Сегодня
                </button>
              )}
            </div>
          </div>

          <div className="grid stats stagger mt-4">
            {stats &&
              stats.map((s) => (
                <div className="card hover" key={s.label}>
                  <span className="tx-icon">
                    <Icon name={s.icon} size={18} />
                  </span>
                  <div className="muted mt-3">
                    {s.label}
                  </div>
                  <div className="stat-value">
                    {s.value}
                  </div>
                </div>
              ))}

            {/* ручной штраф за смену: менеджер правит, линейный видит если задан */}
            {(canEdit || Number(shift.manual_penalty) > 0) && (
              <div className="card hover">
                <span className="tx-icon">
                  <Icon name="minus" size={18} />
                </span>
                <div className="muted mt-3">
                  Штраф за смену
                </div>
                {canEdit && penEdit ? (
                  <div className="wrap mt-2">
                    <input
                      className="input"
                      inputMode="decimal"
                      value={penVal}
                      onChange={(e) => setPenVal(e.target.value)}
                      style={{ width: 92 }}
                      autoFocus
                    />
                    <button
                      className="icon-btn"
                      onClick={saveShiftPenalty}
                      disabled={busy}
                      aria-label="Сохранить штраф"
                    >
                      <Icon name="check" size={16} />
                    </button>
                    <button
                      className="icon-btn"
                      onClick={() => setPenEdit(false)}
                      aria-label="Отмена"
                    >
                      <Icon name="close" size={16} />
                    </button>
                  </div>
                ) : (
                  <div className="between">
                    <div className="stat-value">
                      {fmtMoney(shift.manual_penalty)}
                    </div>
                    {canEdit && (
                      <button
                        className="icon-btn"
                        onClick={() => {
                          setPenVal(String(Number(shift.manual_penalty)));
                          setPenEdit(true);
                        }}
                        aria-label="Изменить штраф"
                      >
                        <Icon name="edit" size={15} />
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="between mt-5">
            <h2 className="section-title m-0">
              В смене ({shift.members_count})
            </h2>
            {canEdit && !adding && (
              <button className="btn sm" onClick={() => setAdding(true)}>
                <Icon name="plus" size={15} /> Поставить в смену
              </button>
            )}
          </div>

          {canEdit && adding && (
            <div className="card mt-3">
              <div className="wrap">
                {free.map((s) => (
                  <button
                    key={s.id}
                    className="btn ghost sm"
                    disabled={busy}
                    onClick={() => changeMember(s.id, true)}
                  >
                    {s.name} · {s.role_display}
                  </button>
                ))}
                {free.length === 0 && (
                  <p className="muted">Все сотрудники уже в смене.</p>
                )}
              </div>
              <button
                className="btn ghost sm mt-3"
                onClick={() => setAdding(false)}
              >
                Отмена
              </button>
            </div>
          )}

          <div className="card mt-3">
            {shift.members.map((m) => (
              <div className="row" key={m.id}>
                <span className="tx-icon">
                  <Icon name="user" size={17} />
                </span>
                <div className="row-body">
                  <strong>
                    {m.name}
                    {m.user === user?.id ? " (вы)" : ""}
                  </strong>
                  <span className="muted">{m.role_display}</span>
                </div>
                <strong className="num">{fmtMoney(m.payout)}</strong>
                {canEdit && (
                  <button
                    className="icon-btn"
                    disabled={busy}
                    aria-label="Убрать из смены"
                    onClick={() => changeMember(m.user, false)}
                  >
                    <Icon name="minus" size={16} />
                  </button>
                )}
              </div>
            ))}
            {shift.members.length === 0 && (
              <p className="muted">
                {canEdit
                  ? "Никого нет — поставьте смену."
                  : "На этот день смену ещё не поставили."}
              </p>
            )}
          </div>

          <p className="muted mt-3">
            Оплата за день {fmtMoney(shift.daily_rate)} + бонус{" "}
            {fmtMoney(shift.bonus_percent)}% от выручки на всех
            {shift.penalty_table
              ? ` − списания со стола «${shift.penalty_table}»`
              : ""}
            . Ставку, процент и штрафной стол задаёт админ.
          </p>
        </>
      )}

      {/* ——— период ——— */}
      {tab !== "day" && (
        <div className="wrap mt-3">
          <input
            className="input"
            style={{ width: 160 }}
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
          <input
            className="input"
            style={{ width: 160 }}
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </div>
      )}

      {/* ——— история смен ——— */}
      {tab === "history" && (
        <div className="stack loose mt-4">
          {history.map((s) => (
            <div className="card" key={s.date}>
              <div className="between">
                <strong className="title">
                  {fmtDay(s.date)}
                </strong>
                {/* выручку дня видит только менеджер/админ */}
                {isManager && (
                  <span className="chip">
                    <Icon name="chart" size={15} />
                    <span className="num">{fmtMoney(s.revenue)}</span>
                  </span>
                )}
              </div>
              <div className="wrap mt-3">
                {s.members.map((m) => (
                  <span className="badge open" key={m.id}>
                    {m.name} · {m.role_display}
                  </span>
                ))}
              </div>
              <div className="row mt-2">
                <span className="muted">
                  ставка {fmtMoney(s.daily_rate)} + бонус {fmtMoney(s.bonus_share)}
                  {Number(s.penalty) > 0
                    ? ` − списания ${fmtMoney(s.penalty_share)}`
                    : ""}
                </span>
                <strong className="num">{fmtMoney(s.payout)}</strong>
              </div>
            </div>
          ))}
          {history.length === 0 && (
            <p className="muted">За этот период смен нет.</p>
          )}
        </div>
      )}

      {/* ——— к выплате ——— */}
      {tab === "payroll" && (
        <div className="card mt-4">
          {payroll?.rows.map((r) => (
            <div className="row" key={r.user}>
              <span className="tx-icon">
                <Icon name="user" size={17} />
              </span>
              <div className="row-body">
                <strong>{r.name}</strong>
                <span className="muted">
                  {r.role_display} · {fmtDays(r.days)} · ставка {fmtMoney(r.base)} +
                  бонус {fmtMoney(r.bonus)}
                  {Number(r.penalty) > 0
                    ? ` − списания ${fmtMoney(r.penalty)}`
                    : ""}
                </span>
              </div>
              <strong className="num lg text-brand">
                {fmtMoney(r.total)}
              </strong>
            </div>
          ))}
          {!payroll?.rows.length && (
            <p className="muted">За этот период выплат нет.</p>
          )}
        </div>
      )}

    </>
  );
}
