import { useEffect, useMemo, useRef, useState } from "react";
import { get, post, del, ApiError } from "../../api";
import { decimalInput } from "../../decimal";
import type { Recipe, StockItem, WriteOff } from "../../types";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";
import { fmtDateTime } from "../../time";

type Line = { item: number | ""; quantity: string };

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
  const [title, setTitle] = useState("");
  const [reason, setReason] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const formRef = useRef<HTMLDivElement>(null);

  // подстановка состава из тех карты
  const [recipes, setRecipes] = useState<Recipe[] | null>(null);
  const [dish, setDish] = useState<number | "">("");
  const [portions, setPortions] = useState("1");

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

  function openForm(from?: WriteOff) {
    setTitle(from?.title ?? "");
    setReason(from?.reason ?? "");
    setLines(
      from
        ? from.items.map((i) => ({ item: i.item, quantity: String(Number(i.quantity)) }))
        : [{ item: "", quantity: "" }]
    );
    setDish("");
    setPortions("1");
    setOpen(true);
    if (recipes === null) {
      get<Recipe[]>("/inventory/recipes/")
        .then(setRecipes)
        .catch(() => setRecipes([]));
    }
  }

  /** Состав блюда из тех карты × порции. Подставляется целиком —
   *  дальше строки можно править руками. */
  function fillFromCard(variant: number | "", count: string) {
    setDish(variant);
    setPortions(count);
    const card = withCard.find((r) => r.variant === variant);
    if (!card) return;
    const n = Number(count) > 0 ? Number(count) : 1;
    setTitle(n === 1 ? card.product_name : `${card.product_name} × ${fmtQty(n)}`);
    setLines(
      card.lines.map((l) => ({
        item: l.item,
        quantity: String(Math.round(Number(l.quantity) * n * 1000) / 1000),
      }))
    );
  }

  function setLine(idx: number, patch: Partial<Line>) {
    setLines((ls) => ls.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  }

  const ready = lines.filter((l) => l.item !== "" && Number(l.quantity) > 0);
  // Ориентир по деньгам — по последней цене закупки, как посчитает и сервер.
  const estimate = ready.reduce((s, l) => {
    const cost = itemById[l.item as number]?.last_unit_cost;
    return cost ? s + Number(cost) * Number(l.quantity) : s;
  }, 0);

  async function submit() {
    if (!title.trim()) return notify("Напишите, что списываете", "bad");
    if (!reason.trim()) return notify("Укажите, за что списание", "bad");
    if (!ready.length) return notify("Добавьте хотя бы один товар", "bad");
    setSubmitting(true);
    try {
      const created = await post<WriteOff>("/inventory/write-offs/", {
        title: title.trim(),
        reason: reason.trim(),
        items: ready.map((l) => ({ item: l.item, quantity: l.quantity })),
      });
      if (month === thisMonth) setList((ws) => [created, ...ws]);
      else setMonth(thisMonth);
      setOpen(false);
      onChange();
      notify("Списано", "ok");
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
            Остатки уменьшатся по товарам ниже. Блюдо с тех картой можно выбрать —
            состав подставится сам; заготовку без карты распишите по ингредиентам.
          </p>

          {withCard.length > 0 && (
            <div className="receipt-line two mt-3">
              <select
                className="input"
                value={dish}
                onChange={(e) =>
                  fillFromCard(e.target.value ? Number(e.target.value) : "", portions)
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
                value={portions}
                onChange={(e) => {
                  const v = decimalInput(e.target.value);
                  if (dish !== "") fillFromCard(dish, v);
                  else setPortions(v);
                }}
                placeholder="порций"
                title="Сколько порций"
              />
              <span className="muted sm">порц.</span>
            </div>
          )}

          <div className="grid cols-2 mt-3">
            <label className="field">
              <span className="label">Что списываем</span>
              <input
                className="input"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="напр. Сливочная шапка"
              />
            </label>
            <label className="field">
              <span className="label">За что</span>
              <input
                className="input"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="напр. не продали за день"
              />
            </label>
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

          <div className="stack mt-3">
            {lines.map((l, idx) => {
              const it = l.item !== "" ? itemById[l.item] : null;
              return (
                <div key={idx} className="receipt-line two">
                  <select
                    className="input"
                    value={l.item}
                    onChange={(e) => setLine(idx, { item: Number(e.target.value) })}
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
                    onChange={(e) => setLine(idx, { quantity: decimalInput(e.target.value) })}
                    placeholder={it ? it.unit_display : "кол-во"}
                  />
                  <button
                    className="icon-btn danger"
                    onClick={() => setLines((ls) => ls.filter((_, i) => i !== idx))}
                    aria-label="Убрать строку"
                  >
                    <Icon name="trash" size={16} />
                  </button>
                </div>
              );
            })}
            <button
              className="btn sm ghost self-start"
              onClick={() => setLines((ls) => [...ls, { item: "", quantity: "" }])}
            >
              <Icon name="plus" size={15} /> Товар
            </button>
          </div>

          {estimate > 0 && (
            <span className="muted sm mt-3">
              ≈ {fmtMoney(estimate)} ₽ по последним ценам закупки
            </span>
          )}

          <button className="btn block mt-4" onClick={submit} disabled={submitting}>
            <Icon name={submitting ? "spark" : "check"} size={18} /> Списать
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
