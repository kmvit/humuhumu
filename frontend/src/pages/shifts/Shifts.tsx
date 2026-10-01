import { useCallback, useEffect, useMemo, useState } from "react";
import { get, patch, post, ApiError } from "../../api";
import type {
  PaySettings,
  Payroll,
  Role,
  Shift,
  ShiftMember,
  ShiftOptions,
  StaffUser,
} from "../../types";
import Icon, { type IconName } from "../../components/Icon";
import { useAuth } from "../../auth";
import { useToast } from "../../components/ui/Toast";
import Modal from "../../components/ui/Modal";
import PayRules, { fmtHours } from "./PayRules";
import { payParts } from "./pay";
import FocusTab from "./FocusTab";

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

// 1 заказ, 2 заказа, 5 заказов
function fmtOrders(n: number): string {
  const ten = n % 100;
  const one = n % 10;
  if (ten >= 11 && ten <= 14) return `${n} заказов`;
  if (one === 1) return `${n} заказ`;
  if (one >= 2 && one <= 4) return `${n} заказа`;
  return `${n} заказов`;
}

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
  const [tab, setTab] = useState<"day" | "history" | "payroll" | "focus" | "pay">("day");
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
  // правила оплаты (ставка, процент, штрафной стол) — вкладка владельца
  const [pay, setPay] = useState<PaySettings | null>(null);
  // типы смен и роли — из них менеджер выбирает, ставя человека в смену
  const [options, setOptions] = useState<ShiftOptions | null>(null);
  const [addType, setAddType] = useState<number | "">("");
  const [editing, setEditing] = useState<ShiftMember | null>(null);
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

  const loadOptions = useCallback(() => {
    get<ShiftOptions>("/shifts/options/")
      .then((o) => {
        setOptions(o);
        // по умолчанию — первый тип, как и на сервере
        setAddType((cur) =>
          cur !== "" && o.shift_types.some((t) => t.id === cur)
            ? cur
            : (o.shift_types[0]?.id ?? "")
        );
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (canEdit && !options) loadOptions();
  }, [canEdit, options, loadOptions]);

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
          ...(add && addType !== "" ? { shift_type: addType } : {}),
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

  // Грузим только когда владелец открыл вкладку: остальным ручка закрыта,
  // и лишний 403 на каждом заходе в раздел ни к чему.
  useEffect(() => {
    if (tab !== "pay" || pay) return;
    get<PaySettings>("/shifts/settings/").then(setPay).catch(() => {});
  }, [tab, pay]);

  async function savePaySettings(next: Partial<PaySettings>) {
    setBusy(true);
    try {
      setPay(await patch<PaySettings>("/shifts/settings/", next));
      notify("Правила оплаты сохранены", "ok");
      // Ставка применяется и к сегодняшней смене — перечитываем цифры.
      loadDay(day).catch(() => {});
      loadPeriod().catch(() => {});
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не получилось", "bad");
    } finally {
      setBusy(false);
    }
  }

  async function saveMember(userId: number, body: Record<string, unknown>) {
    setBusy(true);
    try {
      setShift(await post<Shift>("/shifts/update_member/", { user: userId, date: day, ...body }));
      setEditing(null);
      notify("Сохранено", "ok");
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

  // Кто сколько сделал за день: состав смены плюс те, кто работал, но в
  // смену не поставлен, — их работа не должна пропадать из виду.
  const made = useMemo(() => {
    const rows = [
      ...(shift?.members ?? []).map((m) => ({
        user: m.user,
        name: m.name,
        orders: m.orders,
        orders_total: m.orders_total,
        outsider: false,
      })),
      ...(shift?.outsiders ?? []).map((o) => ({ ...o, outsider: true })),
    ].sort((a, b) => Number(b.orders_total) - Number(a.orders_total));
    return {
      rows,
      orders: rows.reduce((n, r) => n + r.orders, 0),
      total: rows.reduce((n, r) => n + Number(r.orders_total), 0),
    };
  }, [shift]);

  if (loading) return <p className="muted">Загрузка…</p>;

  // Выручку дня видит только менеджер/админ — линейному персоналу оставляем
  // бонус и его выплату, но не общую выручку заведения.
  // В оплате за результат у каждого своя сумма: менеджер видит итог
  // смены, работник — свою выплату. «На человека» там смысла не имеет.
  const result = shift?.scheme === "result";
  const mine = shift?.members.find((m) => m.user === user?.id);
  const stats: { icon: IconName; label: string; value: string; hint?: string }[] | null = shift
    ? [
        ...(isManager
          ? [{ icon: "chart" as IconName, label: "Выручка за день", value: fmtMoney(shift.revenue) }]
          : []),
        ...(result
          ? []
          : [
              {
                icon: "spark" as IconName,
                label: `Бонус ${fmtMoney(shift.bonus_percent)}% на всех`,
                value: fmtMoney(shift.bonus_pool),
              },
            ]),
        { icon: "gift", label: "Списания (подарки)", value: fmtMoney(shift.penalty) },
        !result
          ? { icon: "wallet", label: "К выплате на человека", value: fmtMoney(shift.payout) }
          : isManager
            ? { icon: "wallet", label: "К выплате за смену", value: fmtMoney(shift.payout_total) }
            : { icon: "wallet", label: "Моя выплата", value: fmtMoney(mine?.payout ?? 0) },
        // Свой КПД и сколько до следующей ступени — ради этого экрана
        // система и затевалась: человек видит, за что получает.
        ...(result && mine?.kpi != null
          ? [
              {
                icon: "spark" as IconName,
                label: `Мой КПД · бонус ${fmtMoney(mine.kpi_bonus)}`,
                value: `${fmtMoney(mine.kpi)} ₽/ч`,
                hint: kpiHint(mine.kpi, mine.kpi_next),
              },
            ]
          : []),
        ...(result && mine
          ? [
              {
                icon: "heart" as IconName,
                label: `Фокус · бонус ${fmtMoney(mine.focus_bonus)}`,
                value: `${mine.focus_count} шт`,
                hint: mine.focus_items.length
                  ? mine.focus_items.map((f) => `${f.title} × ${f.count}`).join(", ")
                  : "Список — во вкладке «Фокус»",
              },
              {
                icon: "coffee" as IconName,
                label: `Допродажи · бонус ${fmtMoney(mine.upsell_bonus)}`,
                value: `${mine.upsell_count} шт`,
                hint: mine.upsell_items.length
                  ? mine.upsell_items.map((f) => `${f.title} × ${f.count}`).join(", ")
                  : "Сиропы, альтернативное молоко и другие добавки к заказу",
              },
            ]
          : []),
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
        {/* Фокусные позиции платят только в оплате за результат —
            при оплате поровну вкладка только сбивала бы с толку. */}
        {shift?.scheme === "result" && (
          <button
            className={"navlink" + (tab === "focus" ? " active" : "")}
            onClick={() => setTab("focus")}
          >
            <Icon name="spark" size={16} /> Фокус
          </button>
        )}
        {/* Ставка и процент — деньги персонала, их задаёт владелец.
            Менеджер ставит состав смены, но не цену рабочего дня. */}
        {user?.role === "admin" && (
          <button
            className={"navlink" + (tab === "pay" ? " active" : "")}
            onClick={() => setTab("pay")}
          >
            <Icon name="cash" size={16} /> Оплата
          </button>
        )}
      </div>

      {tab === "focus" && (
        <FocusTab
          onChanged={() => {
            loadDay(day).catch(() => {});
            loadPeriod().catch(() => {});
          }}
        />
      )}

      {/* ——— правила оплаты (владелец) ——— */}
      {tab === "pay" &&
        (pay === null ? (
          <p className="muted mt-3">Загрузка…</p>
        ) : (
          <PayRules
            pay={pay}
            setPay={setPay}
            busy={busy}
            onSave={savePaySettings}
            onChanged={() => {
              loadOptions();
              loadDay(day).catch(() => {});
              loadPeriod().catch(() => {});
            }}
          />
        ))}

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
                        ? `${c.info.members_count} чел` +
                          (c.info.revenue != null ? ` · выручка ${fmtMoney(c.info.revenue)}` : "")
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
                  {s.hint && <div className="muted sm mt-1">{s.hint}</div>}
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
              <div className="field">
                {!!options?.shift_types.length && (
                  <label>
                    <span className="label">Тип смены</span>
                    <select
                      className="input"
                      value={addType}
                      onChange={(e) =>
                        setAddType(e.target.value === "" ? "" : Number(e.target.value))
                      }
                    >
                      {options.shift_types.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name} · {t.starts_at}–{t.ends_at}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {/* Здесь тип выбирают, а заводят и правят — во вкладке «Оплата»:
                    у типа есть ставки, а это решение владельца, не менеджера. */}
                {user?.role === "admin" ? (
                  <button className="btn ghost sm mt-2" onClick={() => setTab("pay")}>
                    <Icon name="edit" size={15} />{" "}
                    {options?.shift_types.length ? "Изменить типы смен" : "Завести типы смен"}
                  </button>
                ) : (
                  <span className="muted sm">
                    Типы смен и их время задаёт админ во вкладке «Оплата». Время
                    конкретного человека можно поправить карандашом в его строке.
                  </span>
                )}
              </div>
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
                  <span className="muted">
                    {m.role_display}
                    {m.starts_at && m.ends_at
                      ? ` · ${m.shift_type_name ? m.shift_type_name + " " : ""}${m.starts_at}–${m.ends_at} (${fmtHours(m.hours)})`
                      : m.shift_type_name
                        ? ` · ${m.shift_type_name}`
                        : ""}
                    {m.orders > 0
                      ? ` · ${m.orders} зак. на ${fmtMoney(m.orders_total)}`
                      : ""}
                  </span>
                  {/* оплата по часам: видно, что задержался или ушёл раньше */}
                  {m.paid_hours != null &&
                    m.planned_hours != null &&
                    Number(m.paid_hours) !== Number(m.planned_hours) && (
                      <span className="muted">
                        К оплате {fmtHours(m.paid_hours)} из {fmtHours(m.planned_hours)} по
                        плану
                        {m.hourly != null ? ` · ${fmtMoney(m.hourly)} ₽/ч` : ""}
                      </span>
                    )}
                  {m.kpi != null && (
                    <span className="muted">
                      КПД {fmtMoney(m.kpi)} ₽/ч ({fmtMoney(m.kpi_revenue)} за{" "}
                      {fmtHours(m.hours)}) → +{fmtMoney(m.kpi_bonus)}
                    </span>
                  )}
                  {result && m.focus_count > 0 && (
                    <span className="muted">
                      Фокус: {m.focus_items.map((f) => `${f.title} × ${f.count}`).join(", ")} → +
                      {fmtMoney(m.focus_bonus)}
                    </span>
                  )}
                  {result && m.upsell_count > 0 && (
                    <span className="muted">
                      Допродажи:{" "}
                      {m.upsell_items.map((f) => `${f.title} × ${f.count}`).join(", ")} → +
                      {fmtMoney(m.upsell_bonus)}
                    </span>
                  )}
                  {m.time_missing ? (
                    <span className="label-bad">
                      Время не отмечено — оплата по плану смены
                      {m.in_kpi ? ", КПД не посчитать" : ""}
                    </span>
                  ) : (
                    m.in_kpi &&
                    result &&
                    !m.hours && (
                      <span className="muted">Нет времени смены — КПД не посчитать</span>
                    )
                  )}
                  {m.is_senior && (
                    <span className="badge open mini mt-1" style={{ alignSelf: "flex-start" }}>
                      Старший
                    </span>
                  )}
                </div>
                {/* у коллег сотруднику сумма не приходит — это их личное */}
                {m.payout != null && <strong className="num">{fmtMoney(m.payout)}</strong>}
                {canEdit && (
                  <button
                    className="icon-btn"
                    disabled={busy}
                    aria-label="Изменить"
                    onClick={() => setEditing(m)}
                  >
                    <Icon name="edit" size={15} />
                  </button>
                )}
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
            {shift.outsiders?.map((o) => (
              <div className="row" key={`out-${o.user}`}>
                <span className="tx-icon">
                  <Icon name="user" size={17} />
                </span>
                <div className="row-body">
                  <strong>{o.name}</strong>
                  <span className="muted">
                    Не в смене · {o.orders} зак. на {fmtMoney(o.orders_total)}
                  </span>
                </div>
                {canEdit && (
                  <button
                    className="icon-btn"
                    disabled={busy}
                    aria-label="Поставить в смену"
                    onClick={() => changeMember(o.user, true)}
                  >
                    <Icon name="plus" size={16} />
                  </button>
                )}
              </div>
            ))}
            {/* Заказы, закрытые, когда на смене не было ни одного участника
                КПД, никому не засчитаны. Молча делить их нельзя, прятать —
                тоже: обычно это значит, что время в смене стоит неверно. */}
            {isManager && result && Number(shift.kpi_unassigned) > 0 && (
              <p className="muted sm m-0 mt-2">
                Заказы на {fmtMoney(shift.kpi_unassigned)} ₽ закрыты, когда на смене не
                было никого из делящих выручку, — в КПД они не попали. Проверьте время
                прихода и ухода.
              </p>
            )}
            {shift.members.length === 0 && (
              <p className="muted">
                {canEdit
                  ? "Никого нет — поставьте смену."
                  : "На этот день смену ещё не поставили."}
              </p>
            )}
          </div>

          {/* ——— кто сколько сделал ———
              Ради этих цифр бариста и отмечает себя на заказе: по ним
              владелец решает, переходить ли на сдельную оплату. Смотреть
              их было негде, поэтому здесь они отдельным блоком, а не
              припиской к строке состава. */}
          {made.rows.length > 0 && (
            <div className="card mt-3">
              <div className="between">
                <strong className="title">Кто сколько сделал</strong>
                <span className="chip sm">
                  {made.orders} зак. · {fmtMoney(made.total)} ₽
                </span>
              </div>
              <p className="muted subtitle m-0">
                Закрытые заказы с отметкой исполнителя.{" "}
                {result
                  ? "По ней засчитываются фокусные позиции и допродажи; КПД считается по времени смены."
                  : "На выплату пока не влияет."}
              </p>

              {made.rows.map((r) => (
                <div className="row" key={`made-${r.user}`}>
                  <span className="tx-icon">
                    <Icon name="user" size={17} />
                  </span>
                  <div className="row-body">
                    <strong>
                      {r.name}
                      {r.user === user?.id ? " (вы)" : ""}
                    </strong>
                    <span className="muted">
                      {r.orders > 0
                        ? `${fmtOrders(r.orders)} · ${
                            made.total > 0
                              ? Math.round((Number(r.orders_total) / made.total) * 100)
                              : 0
                          }% выручки смены`
                        : "пока ни одного заказа"}
                      {r.outsider ? " · не в смене" : ""}
                    </span>
                  </div>
                  <strong className="num">{fmtMoney(r.orders_total)}</strong>
                  {r.outsider && canEdit && (
                    <button
                      className="icon-btn"
                      disabled={busy}
                      aria-label="Поставить в смену"
                      onClick={() => changeMember(r.user, true)}
                    >
                      <Icon name="plus" size={16} />
                    </button>
                  )}
                </div>
              ))}

              {made.orders === 0 && (
                <p className="muted sm m-0 mt-2">
                  Никто не отмечался на заказах. Исполнитель выбирается на карточке
                  заказа — поле необязательное, поэтому его можно и пропустить.
                </p>
              )}
            </div>
          )}

          <p className="muted mt-3">
            {result ? (
              <>
                {shift.prorate
                  ? "Ставка по типу смены и роли за отработанные часы"
                  : "Ставка по типу смены и роли"}
                {shift.kpi_grid.length ? " + бонус за КПД по сетке" : ""}
                {" + фокусные позиции + допродажи"}
                {Number(shift.senior_bonus) > 0
                  ? ` + ${fmtMoney(shift.senior_bonus)}${shift.prorate ? " ₽/ч" : ""} старшему`
                  : ""}
                {shift.penalty_table
                  ? ` − списания со стола «${shift.penalty_table}»`
                  : ""}
                . Ставки и типы смен задаёт админ.
              </>
            ) : (
              <>
                Оплата за день {fmtMoney(shift.daily_rate)} + бонус{" "}
                {fmtMoney(shift.bonus_percent)}% от выручки на всех
                {shift.penalty_table
                  ? ` − списания со стола «${shift.penalty_table}»`
                  : ""}
                . Ставку, процент и штрафной стол задаёт админ.
              </>
            )}
          </p>

          {editing && options && (
            <MemberEditor
              member={editing}
              options={options}
              isToday={day === today}
              busy={busy}
              onClose={() => setEditing(null)}
              onSave={(body) => saveMember(editing.user, body)}
            />
          )}
        </>
      )}

      {/* ——— период ——— */}
      {/* Правилам оплаты период не нужен: они одни на все смены. */}
      {(tab === "history" || tab === "payroll") && (
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
                    {s.scheme === "result" && m.payout != null ? ` · ${fmtMoney(m.payout)}` : ""}
                  </span>
                ))}
              </div>
              {s.scheme === "result" ? (
                <div className="row mt-2">
                  <span className="muted">
                    {s.payout_total == null ? "моя выплата · " : ""}
                    ставка, КПД, фокус и надбавки — у каждого свои
                    {Number(s.penalty) > 0
                      ? ` − списания ${fmtMoney(s.penalty_share)} с каждого`
                      : ""}
                  </span>
                  {/* менеджеру — итог смены, сотруднику — его собственная выплата */}
                  <strong className="num">
                    {fmtMoney(
                      s.payout_total ?? s.members.find((m) => m.user === user?.id)?.payout
                    )}
                  </strong>
                </div>
              ) : (
                <div className="row mt-2">
                  <span className="muted">
                    ставка {fmtMoney(s.daily_rate)} + бонус {fmtMoney(s.bonus_share)}
                    {Number(s.penalty) > 0
                      ? ` − списания ${fmtMoney(s.penalty_share)}`
                      : ""}
                  </span>
                  <strong className="num">{fmtMoney(s.payout)}</strong>
                </div>
              )}
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
                  {r.role_display} · {fmtDays(r.days)}
                  {Number(r.paid_hours) > 0
                    ? ` (${fmtHours(r.paid_hours)} к оплате)`
                    : Number(r.hours) > 0
                      ? ` (${fmtHours(r.hours)})`
                      : ""}{" "}
                  · {payParts(r)}
                </span>
                {/* Сделанное за период — рядом с выплатой, чтобы одно
                    было видно вместе с другим, когда решают про сдельную. */}
                {r.orders > 0 && (
                  <span className="muted">
                    {fmtOrders(r.orders)} на {fmtMoney(r.orders_total)} ₽
                  </span>
                )}
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

// Менеджер правит человека в смене: тип, роль, время, старший.
function MemberEditor({
  member,
  options,
  isToday,
  busy,
  onClose,
  onSave,
}: {
  member: ShiftMember;
  options: ShiftOptions;
  /** «Ушёл сейчас» имеет смысл только в сегодняшней смене. */
  isToday: boolean;
  busy: boolean;
  onClose: () => void;
  onSave: (body: Record<string, unknown>) => void;
}) {
  const [typeId, setTypeId] = useState<number | "">(member.shift_type ?? "");
  const [role, setRole] = useState<Role>(member.role);
  const [start, setStart] = useState(member.starts_at ?? "");
  const [end, setEnd] = useState(member.ends_at ?? "");
  const [senior, setSenior] = useState(member.is_senior);
  const [leaveError, setLeaveError] = useState("");

  // Сменили тип — время подставляется из него; поправить можно после.
  function pickType(raw: string) {
    const id = raw === "" ? "" : Number(raw);
    setTypeId(id);
    const t = options.shift_types.find((x) => x.id === id);
    if (t) {
      setStart(t.starts_at);
      setEnd(t.ends_at);
    }
  }

  function save() {
    const body: Record<string, unknown> = { role, is_senior: senior };
    if (typeId !== (member.shift_type ?? "")) body.shift_type = typeId === "" ? null : typeId;
    // время шлём всегда после типа: сервер сначала ставит время типа, потом наше
    body.starts_at = start || null;
    body.ends_at = end || null;
    onSave(body);
  }

  return (
    <Modal onClose={onClose} head={<strong className="title">{member.name}</strong>}>
      {options.shift_types.length > 0 && (
        <label className="field">
          <span className="label">Тип смены</span>
          <select className="input" value={typeId} onChange={(e) => pickType(e.target.value)}>
            <option value="">Без типа</option>
            {options.shift_types.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · {t.starts_at}–{t.ends_at}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        <span className="label">Роль в этой смене</span>
        <select className="input" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {options.roles.map((r) => (
            <option key={r.value} value={r.value}>{r.label}</option>
          ))}
        </select>
        <span className="muted sm">Например, официант сегодня за стойкой — ставка бариста.</span>
      </label>
      <div className="wrap">
        <label className="field" style={{ flex: "1 1 120px" }}>
          <span className="label">Пришёл</span>
          <input className="input" type="time" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field" style={{ flex: "1 1 120px" }}>
          <span className="label">Ушёл</span>
          <input className="input" type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      {/* Время ухода теперь деньги (оплата по часам) — отмечаем одним
          нажатием в момент ухода, а не вспоминаем задним числом. */}
      {isToday && (
        <div className="mb-3">
          <button
            className="btn ghost sm"
            disabled={busy}
            onClick={() => {
              const now = new Date();
              const hhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
              // Дневная смена (по плану конец позже начала), а сейчас раньше
              // прихода — это не уход после полуночи, а неверное «Пришёл».
              const dayShift = !member.starts_at || !member.ends_at || member.ends_at > member.starts_at;
              if (start && dayShift && hhmm < start) {
                setLeaveError(`Сейчас ${hhmm}, а пришёл в ${start} — поправьте «Пришёл»`);
                return;
              }
              setEnd(hhmm);
              onSave({ ends_at: hhmm });
            }}
          >
            <Icon name="logout" size={15} /> Ушёл сейчас
          </button>
          {leaveError && <p className="label-bad sm m-0 mt-1">{leaveError}</p>}
        </div>
      )}
      <label className="inline tight">
        <input type="checkbox" checked={senior} onChange={(e) => setSenior(e.target.checked)} />
        <span className="sm">Старший смены</span>
      </label>
      <div className="wrap mt-3">
        <button className="btn sm" disabled={busy} onClick={save}>
          <Icon name="check" size={15} /> Сохранить
        </button>
        <button className="btn ghost sm" onClick={onClose}>
          Отмена
        </button>
      </div>
    </Modal>
  );
}

// «До +900 не хватает 1 000 ₽/ч» — или что выше уже некуда.
function kpiHint(kpi: string | null, next: { from: string; bonus: string } | null): string {
  if (!next) return "Максимальная ступень";
  const gap = Number(next.from) - Number(kpi ?? 0);
  return `До +${fmtMoney(next.bonus)} не хватает ${fmtMoney(gap)} ₽/ч`;
}
