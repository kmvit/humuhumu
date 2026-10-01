import { useState } from "react";
import { del, patch, post, ApiError } from "../../api";
import type { KpiStep, PaySettings, Role, ShiftTypeInfo } from "../../types";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";

// Правила оплаты — вкладка владельца. Схема «поровну» — как раньше:
// ставка за день + процент от выручки. Схема «за результат» — ставка по
// типу смены и роли + надбавка старшему (бонусы за КПД — следующим этапом).
export default function PayRules({
  pay,
  setPay,
  busy,
  onSave,
  onChanged,
}: {
  pay: PaySettings;
  setPay: (p: PaySettings) => void;
  busy: boolean;
  onSave: (next: Partial<PaySettings>) => void;
  /** Типы или ставки поменялись — сегодняшняя смена пересчитана, надо перечитать. */
  onChanged: () => void;
}) {
  const result = pay.scheme === "result";
  // Как сохранено на сервере: переключение галочки меняет смысл надбавки
  // старшему (₽ в час ↔ ₽ за смену) — без явного предупреждения 25 ₽/ч
  // тихо превратились бы в 25 ₽ за всю смену.
  const [savedProrate, setSavedProrate] = useState(pay.prorate);

  function setStep(i: number, step: KpiStep) {
    setPay({ ...pay, kpi_grid: pay.kpi_grid.map((s, j) => (j === i ? step : s)) });
  }

  function toggleKpi(role: Role, on: boolean) {
    const next = on
      ? [...pay.kpi_roles, role]
      : pay.kpi_roles.filter((r) => r !== role);
    setPay({ ...pay, kpi_roles: next });
  }

  return (
    <>
      <div className="card mt-3">
        <strong className="title">Правила оплаты</strong>
        <p className="muted subtitle m-0">
          По ним считается выплата за смену. Правка действует на сегодняшнюю и
          будущие смены; прошлые остаются со своими ставками — это история выплат.
        </p>

        <div className="field mt-3">
          <span className="label">Схема оплаты</span>
          <div className="wrap">
            {pay.schemes.map((s) => (
              <button
                key={s.value}
                className={"btn sm" + (pay.scheme === s.value ? "" : " ghost")}
                onClick={() => setPay({ ...pay, scheme: s.value })}
              >
                {s.label}
              </button>
            ))}
          </div>
          <span className="muted sm">
            {result
              ? "Ставка своя у каждого типа смены и роли. Процента от выручки нет — его заменят бонусы за результат."
              : "Одна ставка за день на всех и процент от выручки, поделённый поровну."}
          </span>
        </div>

        <label className="field">
          <span className="label">
            {result ? "Ставка, если для роли не задана, ₽" : "Оплата за смену, ₽"}
          </span>
          <input
            className="input"
            type="number"
            min={0}
            step="50"
            value={pay.daily_rate}
            onChange={(e) => setPay({ ...pay, daily_rate: e.target.value })}
          />
          <span className="muted sm">
            {result
              ? "Страховка: человек не выйдет на ноль, если клетку в таблице ставок забыли заполнить."
              : "Сколько получает каждый за отработанный день."}
          </span>
        </label>

        {!result && (
          <label className="field">
            <span className="label">Бонус, % от выручки</span>
            <input
              className="input"
              type="number"
              min={0}
              max={100}
              step="0.5"
              value={pay.bonus_percent}
              onChange={(e) => setPay({ ...pay, bonus_percent: e.target.value })}
            />
            <span className="muted sm">
              Процент от выручки дня. Делится поровну на всех в смене.
            </span>
          </label>
        )}

        {result && (
          <>
            <div className="field">
              <label className="inline tight">
                <input
                  type="checkbox"
                  checked={pay.prorate}
                  onChange={(e) => setPay({ ...pay, prorate: e.target.checked })}
                />
                <span>Ставка по отработанным часам</span>
              </label>
              <p className="muted sm m-0 mt-1">
                Ставка смены ÷ часы по плану × отработанные часы, до ближайшего часа:
                задержался — получил больше, ушёл раньше — меньше. 2 200 за 12 ч и
                13 ч работы — 2 383,33. Время прихода и ухода отмечает менеджер.
              </p>
            </div>

            <label className="field">
              <span className="label">
                Надбавка старшему смены, {pay.prorate ? "₽ в час" : "₽ за смену"}
              </span>
              <input
                className="input"
                type="number"
                min={0}
                step="50"
                value={pay.senior_bonus}
                onChange={(e) => setPay({ ...pay, senior_bonus: e.target.value })}
              />
              <span className="muted sm">
                {pay.prorate
                  ? "Сверх ставки за каждый отработанный час тому, кого менеджер отметил старшим. 25 ₽/ч — это 2 500 вместо 2 200 за 12 часов."
                  : "Сверх ставки тому, кого менеджер отметил старшим."}
              </span>
              {pay.prorate !== savedProrate && (
                <p className="label-bad sm m-0 mt-1">
                  {pay.prorate
                    ? "Теперь это сумма в час — проверьте: было за смену."
                    : "Теперь это сумма за смену — проверьте: было в час."}
                </p>
              )}
            </label>

            <div className="field">
              <span className="label">Кто делит выручку (бонус за КПД)</span>
              <div className="wrap">
                {pay.roles.map((r) => (
                  <label className="inline tight" key={r.value}>
                    <input
                      type="checkbox"
                      checked={pay.kpi_roles.includes(r.value)}
                      onChange={(e) => toggleKpi(r.value, e.target.checked)}
                    />
                    <span className="sm">{r.label}</span>
                  </label>
                ))}
              </div>
              <span className="muted sm">
                Остальные получают только ставку. Каждый заказ делится поровну
                между теми из них, кто на смене в момент закрытия счёта.
              </span>
            </div>

            <div className="field">
              <span className="label">Сетка бонуса за КПД</span>
              <span className="muted sm">
                КПД — личная выручка за час. Ступень — «от скольких ₽ в час» (включительно)
                и надбавка за смену.
              </span>
              {pay.kpi_grid.map((step, i) => (
                <div className="wrap mt-2" key={i} style={{ alignItems: "center", flexWrap: "nowrap" }}>
                  <input
                    className="input"
                    type="number"
                    min={0}
                    step="100"
                    placeholder="от, ₽/час"
                    aria-label="От, ₽ в час"
                    style={{ flex: "1 1 0", minWidth: 0 }}
                    value={step.from}
                    onChange={(e) => setStep(i, { ...step, from: e.target.value })}
                  />
                  <span className="muted">→</span>
                  <input
                    className="input"
                    type="number"
                    min={0}
                    step="50"
                    placeholder="надбавка, ₽"
                    aria-label="Надбавка за смену, ₽"
                    style={{ flex: "1 1 0", minWidth: 0 }}
                    value={step.bonus}
                    onChange={(e) => setStep(i, { ...step, bonus: e.target.value })}
                  />
                  <button
                    className="icon-btn"
                    aria-label="Убрать ступень"
                    onClick={() =>
                      setPay({ ...pay, kpi_grid: pay.kpi_grid.filter((_, j) => j !== i) })
                    }
                  >
                    <Icon name="minus" size={16} />
                  </button>
                </div>
              ))}
              <button
                className="btn ghost sm mt-2"
                onClick={() =>
                  setPay({ ...pay, kpi_grid: [...pay.kpi_grid, { from: "", bonus: "" }] })
                }
              >
                <Icon name="plus" size={15} /> Добавить ступень
              </button>
              {pay.kpi_grid.length > 0 && (
                <p className="muted sm m-0 mt-2">{gridSummary(pay.kpi_grid)}</p>
              )}
            </div>
          </>
        )}

        {/* На стойке столов нет вовсе — и штрафному столу там неоткуда взяться. */}
        {pay.tables.length > 0 && (
          <label className="field">
            <span className="label">
              Штрафной стол <span className="muted">— необязательно</span>
            </span>
            <select
              className="input"
              value={pay.penalty_table ?? ""}
              onChange={(e) =>
                setPay({
                  ...pay,
                  penalty_table: e.target.value === "" ? null : Number(e.target.value),
                })
              }
            >
              <option value="">Не использовать</option>
              {pay.tables.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            <span className="muted sm">
              Стол, на который официант оформляет подарки гостям за косяки персонала.
              Сумма его заказов делится на всех в смене и вычитается из оплаты,
              в выручку дня не идёт.
            </span>
          </label>
        )}

        <button
          className="btn sm"
          disabled={busy}
          onClick={() => {
            setSavedProrate(pay.prorate);
            onSave({
              scheme: pay.scheme,
              daily_rate: pay.daily_rate,
              bonus_percent: pay.bonus_percent,
              senior_bonus: pay.senior_bonus,
              prorate: pay.prorate,
              kpi_roles: pay.kpi_roles,
              // пустые строки — недописанные ступени, их не сохраняем
              kpi_grid: pay.kpi_grid.filter((s) => s.from !== "" || s.bonus !== ""),
              penalty_table: pay.penalty_table,
            });
          }}
        >
          <Icon name="check" size={16} /> Сохранить
        </button>
      </div>

      <div className="card mt-3">
        <strong className="title">Типы смен{result ? " и ставки" : ""}</strong>
        <p className="muted subtitle m-0">
          {result
            ? "Время начала и конца — по нему видно, кто с кем работал одновременно. Ставка — за всю смену, своя у каждой роли; пустая клетка — ставка по умолчанию."
            : "Время начала и конца. Ставки по типам работают в схеме «за результат»."}
        </p>
        {pay.shift_types.map((t) => (
          <TypeEditor
            key={t.id}
            type={t}
            pay={pay}
            showRates={result}
            onSaved={(p) => {
              setPay(p);
              onChanged();
            }}
          />
        ))}
        <TypeEditor
          key={`new-${pay.shift_types.length}`}
          type={null}
          pay={pay}
          showRates={result}
          onSaved={(p) => {
            setPay(p);
            onChanged();
          }}
        />
      </div>
    </>
  );
}

// Один тип смены: название, время и ставки по ролям. type=null — форма нового.
function TypeEditor({
  type,
  pay,
  showRates,
  onSaved,
}: {
  type: ShiftTypeInfo | null;
  pay: PaySettings;
  showRates: boolean;
  onSaved: (p: PaySettings) => void;
}) {
  const notify = useToast();
  const [open, setOpen] = useState(false); // у новой формы — свёрнута до нажатия
  const [name, setName] = useState(type?.name ?? "");
  const [start, setStart] = useState(type?.starts_at ?? "08:00");
  const [end, setEnd] = useState(type?.ends_at ?? "20:00");
  const [rates, setRates] = useState<Partial<Record<Role, string>>>(type?.rates ?? {});
  const [busy, setBusy] = useState(false);
  // удаление в два нажатия: нативный confirm в киоск-браузерах подавляется
  const [confirmDel, setConfirmDel] = useState(false);

  async function save() {
    setBusy(true);
    try {
      // пустая строка — очистить клетку; иначе сервер оставил бы старую ставку
      const body = { name, starts_at: start, ends_at: end, rates };
      const res = type
        ? await patch<PaySettings>(`/shifts/types/${type.id}/`, body)
        : await post<PaySettings>("/shifts/types/", body);
      onSaved(res);
      notify(type ? "Тип смены сохранён" : "Тип смены добавлен", "ok");
      if (!type) setOpen(false);
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не получилось", "bad");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!type) return;
    setBusy(true);
    try {
      onSaved(await del<PaySettings>(`/shifts/types/${type.id}/`));
      notify("Тип смены удалён", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не получилось", "bad");
      setBusy(false);
    }
  }

  if (!type && !open) {
    return (
      <button className="btn ghost sm mt-3" onClick={() => setOpen(true)}>
        <Icon name="plus" size={15} /> Добавить тип смены
      </button>
    );
  }

  return (
    <div className="rule-top mt-3 pt-3">
      <div className="wrap">
        <label className="field m-0" style={{ flex: "1 1 160px" }}>
          <span className="label">Название</span>
          <input
            className="input"
            value={name}
            placeholder="Полная"
            maxLength={40}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="field m-0" style={{ flex: "0 1 110px" }}>
          <span className="label">Начало</span>
          <input
            className="input"
            type="time"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label className="field m-0" style={{ flex: "0 1 110px" }}>
          <span className="label">Конец</span>
          <input
            className="input"
            type="time"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
      </div>
      {type && <span className="muted sm">{fmtHours(type.hours)}</span>}

      {showRates && (
        <div className="wrap mt-2">
          {pay.roles.map((r) => (
            <label className="field m-0" key={r.value} style={{ flex: "1 1 110px" }}>
              <span className="label">{r.label}, ₽</span>
              <input
                className="input"
                type="number"
                min={0}
                step="50"
                placeholder={String(Number(pay.daily_rate))}
                value={rates[r.value] ?? ""}
                onChange={(e) => setRates({ ...rates, [r.value]: e.target.value })}
              />
            </label>
          ))}
        </div>
      )}

      <div className="wrap mt-2">
        <button className="btn sm" disabled={busy} onClick={save}>
          <Icon name="check" size={15} /> {type ? "Сохранить" : "Добавить"}
        </button>
        {type ? (
          confirmDel ? (
            <>
              <button className="btn sm danger" disabled={busy} onClick={remove}>
                Да, удалить
              </button>
              <button className="btn ghost sm" onClick={() => setConfirmDel(false)}>
                Отмена
              </button>
            </>
          ) : (
            <button className="btn ghost sm" onClick={() => setConfirmDel(true)}>
              Удалить
            </button>
          )
        ) : (
          <button className="btn ghost sm" onClick={() => setOpen(false)}>
            Отмена
          </button>
        )}
      </div>
    </div>
  );
}

// «12.00» → «12 ч», «9.50» → «9,5 ч»
export function fmtHours(v: string | null | undefined): string {
  return `${Number(v ?? 0).toLocaleString("ru", { maximumFractionDigits: 2 })} ч`;
}

// «до 2 500 ₽/ч — 0 · от 2 500 — 500 · от 3 500 — 700»: сетка словами,
// чтобы владелец сверил её с ТЗ, не считая в уме.
function gridSummary(grid: KpiStep[]): string {
  const steps = grid
    .filter((s) => s.from !== "" && s.bonus !== "")
    .map((s) => ({ from: Number(s.from), bonus: Number(s.bonus) }))
    .sort((a, b) => a.from - b.from);
  if (!steps.length) return "";
  const n = (v: number) => v.toLocaleString("ru", { maximumFractionDigits: 2 });
  const parts = steps.map((s) => `от ${n(s.from)} — ${n(s.bonus)} ₽`);
  if (steps[0].from > 0) parts.unshift(`до ${n(steps[0].from)} ₽/ч — 0`);
  return parts.join(" · ");
}
