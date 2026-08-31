import { useCallback, useEffect, useMemo, useState } from "react";
import { get, post, del, ApiError } from "../../api";
import type {
  Expense,
  ExpenseCategory,
  Expenses,
  Statement,
  StatementDay,
  StatementRow,
} from "../../types";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";

function fmtMoney(v: string | number | null | undefined): string {
  return Number(v ?? 0).toLocaleString("ru", { maximumFractionDigits: 2 });
}

function fmtDays(n: number): string {
  const ten = n % 100;
  const one = n % 10;
  if (ten >= 11 && ten <= 14) return `${n} смен`;
  if (one === 1) return `${n} смена`;
  if (one >= 2 && one <= 4) return `${n} смены`;
  return `${n} смен`;
}

function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString("ru", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
  });
}

/** «2026-08» для запроса; месяц храним как первое число. */
function monthKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthTitle(key: string): string {
  const [y, m] = key.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("ru", {
    month: "long",
    year: "numeric",
  });
}

function shiftMonth(key: string, delta: number): string {
  const [y, m] = key.split("-").map(Number);
  return monthKey(new Date(y, m - 1 + delta, 1));
}

export default function Finance() {
  const toast = useToast();
  const thisMonth = useMemo(() => monthKey(new Date()), []);
  const [month, setMonth] = useState(thisMonth);
  const [tab, setTab] = useState<"payroll" | "expenses">("payroll");
  const [data, setData] = useState<Statement | null>(null);
  const [loading, setLoading] = useState(true);

  // расходы: список за месяц, справочник статей и форма добавления
  const [expenses, setExpenses] = useState<Expenses | null>(null);
  const [cats, setCats] = useState<ExpenseCategory[]>([]);
  const [form, setForm] = useState({ category: "", amount: "", comment: "" });

  // раскрытая расшифровка по дням + черновик суммы выплаты
  const [openUser, setOpenUser] = useState<number | null>(null);
  const [days, setDays] = useState<StatementDay[]>([]);
  const [payFor, setPayFor] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [st, ex] = await Promise.all([
        get<Statement>(`/finance/payroll/?month=${month}`),
        get<Expenses>(`/finance/expenses/?month=${month}`),
      ]);
      setData(st);
      setExpenses(ex);
    } finally {
      setLoading(false);
    }
  }, [month]);

  useEffect(() => {
    load();
    setOpenUser(null);
    setPayFor(null);
  }, [load]);

  useEffect(() => {
    get<ExpenseCategory[]>("/finance/expense-categories/")
      .then((list) => {
        const active = list.filter((c) => c.is_active);
        setCats(active);
        setForm((f) => (f.category ? f : { ...f, category: String(active[0]?.id ?? "") }));
      })
      .catch(() => {});
  }, []);

  async function addExpense() {
    if (!form.category || Number(form.amount) <= 0) return;
    setBusy(true);
    try {
      await post<Expense>("/finance/expenses/", {
        date: new Date().toISOString().slice(0, 10),
        category: Number(form.category),
        amount: form.amount,
        comment: form.comment,
      });
      setForm((f) => ({ ...f, amount: "", comment: "" }));
      setExpenses(await get<Expenses>(`/finance/expenses/?month=${month}`));
      toast("Расход добавлен");
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Не удалось добавить расход");
    } finally {
      setBusy(false);
    }
  }

  async function removeExpense(id: number) {
    setBusy(true);
    try {
      await del(`/finance/expenses/${id}/`);
      setExpenses(await get<Expenses>(`/finance/expenses/?month=${month}`));
      toast("Расход удалён");
    } catch {
      toast("Не удалось удалить");
    } finally {
      setBusy(false);
    }
  }

  async function toggleDays(row: StatementRow) {
    if (openUser === row.user) {
      setOpenUser(null);
      return;
    }
    setOpenUser(row.user);
    setDays([]);
    const res = await get<{ days: StatementDay[] }>(
      `/finance/payroll/days/?user=${row.user}&month=${month}`
    );
    setDays(res.days);
  }

  function startPay(row: StatementRow) {
    setPayFor(payFor === row.user ? null : row.user);
    setAmount(row.left);
  }

  async function submitPay(row: StatementRow) {
    setBusy(true);
    try {
      const res = await post<Statement>("/finance/payroll/pay/", {
        user: row.user,
        amount,
        month,
      });
      setData(res);
      setPayFor(null);
      toast(`Выплата ${fmtMoney(amount)} ₽ отмечена`);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Не удалось отметить выплату");
    } finally {
      setBusy(false);
    }
  }

  async function undoPay(row: StatementRow) {
    setBusy(true);
    try {
      setData(await post<Statement>("/finance/payroll/unpay/", { user: row.user, month }));
      toast("Последняя выплата отменена");
    } catch {
      toast("Не удалось отменить");
    } finally {
      setBusy(false);
    }
  }

  const totals = data?.totals;

  return (
    <>
      <h1 className="h1">Финансы</h1>
      <p className="muted subtitle">
        {tab === "payroll"
          ? "Начисления считаются по сменам, здесь — сколько выплачено и сколько осталось."
          : "Аренда, коммуналка и прочее — всё, что не зарплата и не закуп продуктов."}
      </p>

      <div className="tabs">
        <button
          className={"navlink" + (tab === "payroll" ? " active" : "")}
          onClick={() => setTab("payroll")}
        >
          <Icon name="wallet" size={16} /> Ведомость
        </button>
        <button
          className={"navlink" + (tab === "expenses" ? " active" : "")}
          onClick={() => setTab("expenses")}
        >
          <Icon name="receipt" size={16} /> Расходы
        </button>
      </div>

      {/* ——— выбор месяца ——— */}
      <div className="card mt-3">
        <div className="cal-head">
          <button
            className="icon-btn"
            aria-label="Предыдущий месяц"
            onClick={() => setMonth(shiftMonth(month, -1))}
          >
            <Icon name="chevronLeft" size={16} />
          </button>
          <span className="cal-month">{monthTitle(month)}</span>
          <button
            className="icon-btn"
            aria-label="Следующий месяц"
            disabled={month >= thisMonth}
            onClick={() => setMonth(shiftMonth(month, 1))}
          >
            <Icon name="chevronRight" size={16} />
          </button>
        </div>
      </div>

      {/* ——— итог по заведению ——— */}
      {tab === "payroll" && totals && (
        <div className="card mt-3">
          <div className="row">
            <span className="tx-icon"><Icon name="chart" size={17} /></span>
            <div className="row-body">
              <strong>Начислено за месяц</strong>
              <span className="muted">
                {totals.people ? `${totals.people} чел. · ${fmtDays(totals.shifts)}` : "смен не было"}
              </span>
            </div>
            <strong className="num lg">{fmtMoney(totals.accrued)} ₽</strong>
          </div>
          <div className="row">
            <span className="tx-icon"><Icon name="check" size={17} /></span>
            <div className="row-body"><strong>Выплачено</strong></div>
            <strong className="num">{fmtMoney(totals.paid)} ₽</strong>
          </div>
          <div className="row">
            <span className="tx-icon"><Icon name="wallet" size={17} /></span>
            <div className="row-body"><strong>Осталось выплатить</strong></div>
            <strong className="num lg text-brand">{fmtMoney(totals.left)} ₽</strong>
          </div>
        </div>
      )}

      {/* ——— строки по людям ——— */}
      {tab === "payroll" && (
      <div className="stack loose mt-4">
        {data?.rows.map((r) => (
          <div className="card" key={r.user}>
            <div className="row">
              <span className="tx-icon"><Icon name="user" size={17} /></span>
              <div className="row-body">
                <strong>{r.name}</strong>
                <span className="muted">
                  {r.role_display} · {fmtDays(r.days)} · ставка {fmtMoney(r.base)} + бонус{" "}
                  {fmtMoney(r.bonus)}
                  {Number(r.penalty) > 0 ? ` − списания ${fmtMoney(r.penalty)}` : ""}
                </span>
              </div>
              <div style={{ textAlign: "right" }}>
                <strong className="num lg">{fmtMoney(r.accrued)} ₽</strong>
                <div className="muted sm">
                  {r.settled ? (
                    <span className="badge ready">выплачено</span>
                  ) : (
                    <>осталось <span className="num">{fmtMoney(r.left)} ₽</span></>
                  )}
                </div>
              </div>
            </div>

            <div className="wrap mt-2">
              <button className="btn sm ghost" onClick={() => toggleDays(r)}>
                <Icon name="spark" size={15} />
                {openUser === r.user ? "Скрыть дни" : "По дням"}
              </button>
              {!r.settled && (
                <button className="btn sm" onClick={() => startPay(r)}>
                  <Icon name="cash" size={15} /> Выплатить
                </button>
              )}
              {Number(r.paid) > 0 && (
                <button className="btn sm ghost" disabled={busy} onClick={() => undoPay(r)}>
                  Отменить последнюю
                </button>
              )}
            </div>

            {/* форма выплаты: сумма подставлена как остаток, можно дать аванс */}
            {payFor === r.user && (
              <div className="wrap mt-2">
                <input
                  className="input"
                  style={{ width: 140 }}
                  type="number"
                  min="0"
                  step="0.01"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  aria-label="Сумма выплаты"
                />
                <button className="btn sm" disabled={busy} onClick={() => submitPay(r)}>
                  <Icon name="check" size={15} /> Отметить
                </button>
                <button className="btn sm ghost" onClick={() => setPayFor(null)}>
                  Отмена
                </button>
              </div>
            )}

            {/* расшифровка: из чего сложилась сумма */}
            {openUser === r.user && (
              <ul className="stack tight list mt-3">
                {days.map((d) => (
                  <li key={d.date} className="between">
                    <span className="muted">
                      {fmtDay(d.date)} · {d.members_count} чел. в смене
                    </span>
                    <span>
                      <span className="muted sm">
                        {fmtMoney(d.daily_rate)} + {fmtMoney(d.bonus_share)}
                        {Number(d.penalty_share) + Number(d.manual_penalty_share) > 0
                          ? ` − ${fmtMoney(
                              Number(d.penalty_share) + Number(d.manual_penalty_share)
                            )}`
                          : ""}
                      </span>{" "}
                      <strong className="num">{fmtMoney(d.payout)} ₽</strong>
                    </span>
                  </li>
                ))}
                {days.length === 0 && <li className="muted sm">Загружаем…</li>}
              </ul>
            )}
          </div>
        ))}

        {!loading && !data?.rows.length && (
          <p className="muted center mt-5">В этом месяце смен не было.</p>
        )}
      </div>
      )}

      {/* ——— расходы ——— */}
      {tab === "expenses" && (
        <>
          <div className="card mt-3">
            <div className="row">
              <span className="tx-icon"><Icon name="receipt" size={17} /></span>
              <div className="row-body">
                <strong>Расходов за месяц</strong>
                <span className="muted">
                  {expenses?.by_category.length
                    ? expenses.by_category
                        .map((c) => `${c.name} ${fmtMoney(c.total)}`)
                        .join(" · ")
                    : "пока ничего не внесено"}
                </span>
              </div>
              <strong className="num lg">{fmtMoney(expenses?.total)} ₽</strong>
            </div>
          </div>

          {/* добавление: статья, сумма, комментарий */}
          <div className="card mt-3">
            <div className="wrap">
              <select
                className="input"
                style={{ minWidth: 180 }}
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                aria-label="Статья расходов"
              >
                {cats.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <input
                className="input"
                style={{ width: 130 }}
                type="number"
                min="0"
                step="0.01"
                placeholder="Сумма"
                value={form.amount}
                onChange={(e) => setForm({ ...form, amount: e.target.value })}
                aria-label="Сумма расхода"
              />
              <input
                className="input"
                style={{ flex: 1, minWidth: 160 }}
                type="text"
                maxLength={200}
                placeholder="Комментарий, напр. «август»"
                value={form.comment}
                onChange={(e) => setForm({ ...form, comment: e.target.value })}
                aria-label="Комментарий"
              />
              <button
                className="btn"
                disabled={busy || !form.category || Number(form.amount) <= 0}
                onClick={addExpense}
              >
                <Icon name="plus" size={16} /> Добавить
              </button>
            </div>
            {cats.length === 0 && (
              <p className="muted sm mt-2">
                Статьи расходов заводятся в админке — там же их можно переименовать.
              </p>
            )}
          </div>

          <div className="stack loose mt-3">
            {expenses?.rows.map((e) => (
              <div className="card" key={e.id}>
                <div className="row">
                  <span className="tx-icon"><Icon name="receipt" size={17} /></span>
                  <div className="row-body">
                    <strong>{e.category_name}</strong>
                    <span className="muted">
                      {fmtDay(e.date)}
                      {e.comment ? ` · ${e.comment}` : ""}
                    </span>
                  </div>
                  <strong className="num">{fmtMoney(e.amount)} ₽</strong>
                  <button
                    className="icon-btn sm"
                    title="Удалить расход"
                    aria-label="Удалить расход"
                    disabled={busy}
                    onClick={() => removeExpense(e.id)}
                  >
                    <Icon name="trash" size={15} />
                  </button>
                </div>
              </div>
            ))}
            {!loading && !expenses?.rows.length && (
              <p className="muted center mt-5">В этом месяце расходов пока нет.</p>
            )}
          </div>
        </>
      )}
    </>
  );
}
