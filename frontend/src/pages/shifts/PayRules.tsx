import { useState } from "react";
import { del, patch, post, ApiError } from "../../api";
import type { PaySettings, Role, ShiftTypeInfo } from "../../types";
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
            <label className="field">
              <span className="label">Надбавка старшему смены, ₽</span>
              <input
                className="input"
                type="number"
                min={0}
                step="50"
                value={pay.senior_bonus}
                onChange={(e) => setPay({ ...pay, senior_bonus: e.target.value })}
              />
              <span className="muted sm">
                Сверх ставки тому, кого менеджер отметил старшим.
              </span>
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
                Остальные получают только ставку. Сам бонус за КПД появится
                следующим обновлением.
              </span>
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
          onClick={() =>
            onSave({
              scheme: pay.scheme,
              daily_rate: pay.daily_rate,
              bonus_percent: pay.bonus_percent,
              senior_bonus: pay.senior_bonus,
              kpi_roles: pay.kpi_roles,
              penalty_table: pay.penalty_table,
            })
          }
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
