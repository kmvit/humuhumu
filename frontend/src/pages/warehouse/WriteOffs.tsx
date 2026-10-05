import { useEffect, useMemo, useRef, useState } from "react";
import { get, post, del, ApiError } from "../../api";
import { decimalInput } from "../../decimal";
import type { Recipe, StockItem, WriteOff } from "../../types";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { fmtDateTime } from "../../time";

type Line = { item: number | ""; quantity: string };

/** Позиция в форме: «Сливочная шапка», «Капучино × 2» — со своим составом. */
type Position = {
  key: number;
  title: string;
  /** Блюдо, из тех карты которого подставлен состав. */
  dish: number | "";
  portions: string;
  lines: Line[];
};

let nextKey = 1;
function blankPosition(): Position {
  return { key: nextKey++, title: "", dish: "", portions: "1", lines: [{ item: "", quantity: "" }] };
}


const isReady = (l: Line) => l.item !== "" && Number(l.quantity) > 0;

// Подсказки к «за что» — частые причины одним тапом. Поле при этом
// свободное: причину можно уточнить или написать свою.
const REASONS = [
  "Не продали",
  "Испортилось",
  "Персоналу",
  "Брак, бой",
  // касса не работала — продали, но мимо системы, а со склада списать надо
  "Продали без кассы",
  "Админ",
];

function fmtQty(q: string | number): string {
  return Number(q).toLocaleString("ru", { maximumFractionDigits: 3 });
}

function fmtMoney(n: number): string {
  return n.toLocaleString("ru", { maximumFractionDigits: 2 });
}

/** «2026-10» для запроса. */
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

type Props = {
  items: StockItem[];
  /** Остатки изменились — перечитать их на складе. */
  onChange: () => void;
};

export default function WriteOffs({ items, onChange }: Props) {
  const notify = useToast();
  const thisMonth = useMemo(() => monthKey(new Date()), []);
  const [month, setMonth] = useState(thisMonth);
  const [list, setList] = useState<WriteOff[]>([]);
  const [loading, setLoading] = useState(true);
  const [delId, setDelId] = useState<number | null>(null);

  // форма списания
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [positions, setPositions] = useState<Position[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const formRef = useRef<HTMLDivElement>(null);

  // тех карты — чтобы подставлять состав блюда
  const [recipes, setRecipes] = useState<Recipe[] | null>(null);

  const itemById = useMemo(
    () => Object.fromEntries(items.map((i) => [i.id, i])),
    [items]
  );
  const withCard = useMemo(
    () => (recipes ?? []).filter((r) => r.lines.length > 0),
    [recipes]
  );

  useEffect(() => {
    setLoading(true);
    get<WriteOff[]>(`/inventory/write-offs/?month=${month}`)
      .then(setList)
      .catch((e) =>
        notify(e instanceof ApiError ? e.message : "Не удалось загрузить списания", "bad")
      )
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [month]);

  // «Повторить» жмут внизу журнала — форма открывается сверху, подводим к ней
  useEffect(() => {
    if (open) formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [open]);

  const monthTotal = list.reduce((s, w) => s + Number(w.total_cost), 0);

  /** Строки только по товарам, которые есть на складе.
   *
   *  Удалённый товар со склада прячется, но в старых списаниях и тех
   *  картах остаётся. Пусти его в форму — в выпадающем списке его нет, и
   *  браузер показал бы первый попавшийся товар: человек видит «Зерно», а
   *  списывается скрытое молоко. Поэтому такие строки не берём и говорим. */
  function activeLines(
    src: { item: number; item_name: string; quantity: string | number }[],
    multiplier = 1,
    warn = true
  ): Line[] {
    const gone = src.filter((l) => !itemById[l.item]).map((l) => l.item_name);
    if (gone.length && warn) {
      notify(`Не подставлено — товара больше нет на складе: ${gone.join(", ")}`, "bad");
    }
    return src
      .filter((l) => itemById[l.item])
      .map((l) => ({
        item: l.item,
        quantity: String(Math.round(Number(l.quantity) * multiplier * 1000) / 1000),
      }));
  }

  function fromWriteOff(w: WriteOff): Position {
    const lines = activeLines(w.items);
    return {
      ...blankPosition(),
      title: w.title,
      lines: lines.length ? lines : [{ item: "", quantity: "" }],
    };
  }

  /** Открыть форму; с `from` — добавить позицию как в прошлом списании.
   *  Если форма уже открыта, позиция дописывается к набранным: так за
   *  пару нажатий «Повторить» собирается всё, что списывают каждый день. */
  function openForm(from?: WriteOff) {
    if (open && from) {
      const added = fromWriteOff(from); // вне setState: там же предупреждение
      setPositions((ps) => {
        // пустую заготовку формы заменяем, а не оставляем висеть сверху
        const filled = ps.filter((p) => p.title.trim() || p.lines.some(isReady));
        return [...filled, added];
      });
      if (!reason.trim()) setReason(from.reason);
      notify(`Добавлено: ${from.title}`, "ok");
      return;
    }
    setReason(from?.reason ?? "");
    setPositions([from ? fromWriteOff(from) : blankPosition()]);
    setOpen(true);
    if (recipes === null) {
      get<Recipe[]>("/inventory/recipes/")
        .then(setRecipes)
        .catch(() => setRecipes([]));
    }
  }

  function setPos(key: number, patch: Partial<Position>) {
    setPositions((ps) => ps.map((p) => (p.key === key ? { ...p, ...patch } : p)));
  }

  function setLine(key: number, idx: number, patch: Partial<Line>) {
    setPositions((ps) =>
      ps.map((p) =>
        p.key === key
          ? { ...p, lines: p.lines.map((l, i) => (i === idx ? { ...l, ...patch } : l)) }
          : p
      )
    );
  }

  /** Состав блюда из тех карты × порции. Подставляется целиком —
   *  дальше строки можно править руками. */
  function fillFromCard(key: number, variant: number | "", count: string, warn = true) {
    const card = withCard.find((r) => r.variant === variant);
    if (!card) {
      setPos(key, { dish: variant, portions: count });
      return;
    }
    const n = Number(count) > 0 ? Number(count) : 1;
    const lines = activeLines(card.lines, n, warn);
    setPos(key, {
      dish: variant,
      portions: count,
      title: n === 1 ? card.product_name : `${card.product_name} × ${fmtQty(n)}`,
      lines: lines.length ? lines : [{ item: "", quantity: "" }],
    });
  }

  /** Ориентир по деньгам — по последней цене закупки, как посчитает и сервер. */
  function estimateOf(lines: Line[]): number {
    return lines.filter(isReady).reduce((s, l) => {
      const cost = itemById[l.item as number]?.last_unit_cost;
      return cost ? s + Number(cost) * Number(l.quantity) : s;
    }, 0);
  }

  // Совсем пустую позицию (лишний раз нажали «+ Позиция») молча пропускаем,
  // а начатую, но недозаполненную — не пропускаем: о ней скажем.
  const filled = positions.filter((p) => p.title.trim() || p.lines.some(isReady));
  const estimate = filled.reduce((s, p) => s + estimateOf(p.lines), 0);

  async function submit() {
    if (!reason.trim()) return notify("Укажите, за что списание", "bad");
    if (!filled.length) return notify("Добавьте хотя бы одну позицию", "bad");
    for (const [n, p] of filled.entries()) {
      const label = filled.length > 1 ? `Позиция ${n + 1}: ` : "";
      if (!p.title.trim()) return notify(`${label}напишите, что списываете`, "bad");
      if (!p.lines.some(isReady))
        return notify(`${label}добавьте товар и количество`, "bad");
    }
    setSubmitting(true);
    try {
      const created = await post<WriteOff[]>("/inventory/write-offs/batch/", {
        reason: reason.trim(),
        positions: filled.map((p) => ({
          title: p.title.trim(),
          items: p.lines.filter(isReady).map((l) => ({ item: l.item, quantity: l.quantity })),
        })),
      });
      // сервер отдаёт в порядке формы, журнал — новые сверху
      if (month === thisMonth) setList((ws) => [...[...created].reverse(), ...ws]);
      else setMonth(thisMonth);
      setOpen(false);
      onChange();
      notify(created.length > 1 ? `Списано позиций: ${created.length}` : "Списано", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось списать", "bad");
    } finally {
      setSubmitting(false);
    }
  }

  async function remove(id: number) {
    try {
      await del(`/inventory/write-offs/${id}/`);
      setList((ws) => ws.filter((w) => w.id !== id));
      setDelId(null);
      onChange();
      notify("Списание удалено, товары вернулись в остатки", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось удалить", "bad");
    }
  }

  return (
    <div className="stack loose mt-4">
      {/* ——— месяц и итог ——— */}
      <div className="card">
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
        <div className="between mt-3">
          <span className="muted">
            {list.length
              ? `Списаний: ${list.length}`
              : loading
                ? "Загружаю…"
                : "Списаний нет"}
          </span>
          <strong className="num">{fmtMoney(monthTotal)} ₽</strong>
        </div>
      </div>

      {!open && (
        <button className="btn self-start" onClick={() => openForm()} disabled={!items.length}>
          <Icon name="minus" size={18} /> Списать
        </button>
      )}

      {/* ——— форма ——— */}
      {open && (
        <div className="card enter" ref={formRef}>
          <div className="between">
            <strong className="title">Новое списание</strong>
            <button className="btn sm ghost" onClick={() => setOpen(false)}>
              Отмена
            </button>
          </div>
          <p className="muted sm subtitle">
            Можно сразу несколько позиций — каждая уйдёт в журнал отдельно. Блюдо
            с тех картой выберите из списка, состав подставится сам; заготовку без
            карты распишите по ингредиентам.
          </p>

          <label className="field mt-3">
            <span className="label">За что — для всех позиций</span>
            <input
              className="input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="напр. не продали за день"
            />
          </label>
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

          {positions.map((p, n) => {
            const sum = estimateOf(p.lines);
            return (
              <div className="wo-pos mt-3" key={p.key}>
                <div className="between">
                  <strong>{positions.length > 1 ? `Позиция ${n + 1}` : "Позиция"}</strong>
                  {positions.length > 1 && (
                    <button
                      className="btn sm ghost"
                      onClick={() => setPositions((ps) => ps.filter((x) => x.key !== p.key))}
                    >
                      <Icon name="close" size={14} /> Убрать
                    </button>
                  )}
                </div>

                {withCard.length > 0 && (
                  <div className="receipt-line two mt-2">
                    <select
                      className="input"
                      value={p.dish}
                      onChange={(e) =>
                        fillFromCard(p.key, e.target.value ? Number(e.target.value) : "", p.portions)
                      }
                    >
                      <option value="">— взять состав из тех карты —</option>
                      {withCard.map((r) => (
                        <option key={r.variant} value={r.variant}>
                          {r.product_name}
                        </option>
                      ))}
                    </select>
                    <input
                      className="input"
                      inputMode="decimal"
                      value={p.portions}
                      onChange={(e) => {
                        const v = decimalInput(e.target.value);
                        // о пропавших товарах уже сказали при выборе блюда
                        if (p.dish !== "") fillFromCard(p.key, p.dish, v, false);
                        else setPos(p.key, { portions: v });
                      }}
                      placeholder="порций"
                      title="Сколько порций"
                    />
                    <span className="muted sm">порц.</span>
                  </div>
                )}

                <input
                  className="input mt-2"
                  value={p.title}
                  onChange={(e) => setPos(p.key, { title: e.target.value })}
                  placeholder="Что списываем, напр. Сливочная шапка"
                  aria-label="Что списываем"
                />

                <div className="stack mt-2">
                  {p.lines.map((l, idx) => {
                    const it = l.item !== "" ? itemById[l.item] : null;
                    return (
                      <div key={idx} className="receipt-line two">
                        <select
                          className="input"
                          value={l.item}
                          onChange={(e) => setLine(p.key, idx, { item: Number(e.target.value) })}
                        >
                          {l.item === "" && <option value="">— товар склада —</option>}
                          {items.map((i) => (
                            <option key={i.id} value={i.id}>
                              {i.name} ({i.unit_display})
                            </option>
                          ))}
                        </select>
                        <input
                          className="input"
                          inputMode="decimal"
                          value={l.quantity}
                          onChange={(e) =>
                            setLine(p.key, idx, { quantity: decimalInput(e.target.value) })
                          }
                          placeholder={it ? it.unit_display : "кол-во"}
                        />
                        <button
                          className="icon-btn danger"
                          onClick={() =>
                            setPos(p.key, { lines: p.lines.filter((_, i) => i !== idx) })
                          }
                          aria-label="Убрать строку"
                        >
                          <Icon name="trash" size={16} />
                        </button>
                      </div>
                    );
                  })}
                  <div className="between">
                    <button
                      className="btn sm ghost"
                      onClick={() =>
                        setPos(p.key, { lines: [...p.lines, { item: "", quantity: "" }] })
                      }
                    >
                      <Icon name="plus" size={15} /> Товар
                    </button>
                    {sum > 0 && positions.length > 1 && (
                      <span className="muted sm">≈ {fmtMoney(sum)} ₽</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}

          <div className="between mt-3">
            <button
              className="btn sm ghost"
              onClick={() => setPositions((ps) => [...ps, blankPosition()])}
            >
              <Icon name="plus" size={15} /> Позиция
            </button>
            {estimate > 0 && (
              <span className="muted sm">≈ {fmtMoney(estimate)} ₽ по ценам закупки</span>
            )}
          </div>

          <button className="btn block mt-4" onClick={submit} disabled={submitting}>
            <Icon name={submitting ? "spark" : "check"} size={18} />{" "}
            {filled.length > 1 ? `Списать позиций: ${filled.length}` : "Списать"}
          </button>
        </div>
      )}

      {/* ——— журнал ——— */}
      {loading &&
        Array.from({ length: 2 }).map((_, i) => <div className="skeleton sm" key={i} />)}

      {!loading && list.length === 0 && (
        <div className="card center">
          <p className="muted m-0">За {monthTitle(month)} списаний нет.</p>
        </div>
      )}

      {list.map((w) => (
        <div className="card" key={w.id}>
          <div className="between">
            <strong className="title">{w.title}</strong>
            {Number(w.total_cost) > 0 && (
              <strong className="num">{fmtMoney(Number(w.total_cost))} ₽</strong>
            )}
          </div>
          <span className="muted sm">
            {fmtDateTime(w.created_at)}
            {w.created_by_name ? ` · ${w.created_by_name}` : ""} · {w.reason}
          </span>
          <ul className="stack tight list mt-3">
            {w.items.map((li) => (
              <li key={li.id} className="between">
                <span>{li.item_name}</span>
                <span className="num muted">
                  −{fmtQty(li.quantity)} {li.unit_display}
                  {li.unit_cost === null
                    ? " · нет цены закупа"
                    : li.subtotal
                      ? ` · ${fmtMoney(Number(li.subtotal))} ₽`
                      : ""}
                </span>
              </li>
            ))}
          </ul>

          {delId === w.id ? (
            <div className="between mt-3">
              <span className="muted" style={{ minWidth: 0 }}>
                Удалить и вернуть товары в остатки?
              </span>
              <span className="wrap">
                <button className="btn sm ghost" onClick={() => setDelId(null)}>
                  Отмена
                </button>
                <button className="btn sm danger" onClick={() => remove(w.id)}>
                  <Icon name="trash" size={15} /> Удалить
                </button>
              </span>
            </div>
          ) : (
            <div className="wrap mt-3" style={{ justifyContent: "flex-end" }}>
              {/* Сливочную шапку списывают почти каждый день — повтор
                  избавляет от того, чтобы расписывать её заново. */}
              <button className="btn sm ghost" onClick={() => openForm(w)}>
                <Icon name="copy" size={15} /> Повторить
              </button>
              <button className="btn sm ghost" onClick={() => setDelId(w.id)}>
                <Icon name="trash" size={15} /> Удалить
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
