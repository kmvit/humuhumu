import { useCallback, useEffect, useMemo, useState } from "react";
import { get, post, ApiError } from "../../api";
import type { Statement, StatementDay, StatementRow } from "../../types";
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
  const [data, setData] = useState<Statement | null>(null);
  const [loading, setLoading] = useState(true);

  // раскрытая расшифровка по дням + черновик суммы выплаты
  const [openUser, setOpenUser] = useState<number | null>(null);
  const [days, setDays] = useState<StatementDay[]>([]);
  const [payFor, setPayFor] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await get<Statement>(`/finance/payroll/?month=${month}`));
    } finally {
      setLoading(false);
    }
  }, [month]);

  useEffect(() => {
    load();
    setOpenUser(null);
    setPayFor(null);
  }, [load]);

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
      <div className="between">
        <h1 className="h1">Финансы</h1>
        <span className="chip">
          <Icon name="wallet" size={15} /> Ведомость
        </span>
      </div>
      <p className="muted subtitle">
        Начисления считаются по сменам, здесь — сколько выплачено и сколько осталось.
      </p>

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
      {totals && (
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
    </>
  );
}
