import { useEffect, useMemo, useState } from "react";
import { get, put, ApiError } from "../../api";
import { decimalInput } from "../../decimal";
import type { Recipe, RecipeLine, StockItem } from "../../types";
import Icon from "../../components/Icon";
import { useToast } from "../../components/ui/Toast";

type Draft = {
  item: number | "";
  quantity: string;
  comment: string;
  original?: RecipeLine;
};

const emptyLine: Draft = { item: "", quantity: "", comment: "" };

function toDraft(line: RecipeLine): Draft {
  return {
    item: line.item,
    quantity: String(Number(line.quantity)),
    comment: line.comment,
    original: line,
  };
}

function fmtQty(q: string | number): string {
  return Number(q).toLocaleString("ru", { maximumFractionDigits: 3 });
}

type Props = {
  items: StockItem[];
};

export default function Recipes({ items }: Props) {
  const notify = useToast();
  const [cards, setCards] = useState<Recipe[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [onlyEmpty, setOnlyEmpty] = useState(false);

  // редактирование карты одного блюда
  const [editing, setEditing] = useState<{ variant: number; focusLine?: number } | null>(null);
  const [draft, setDraft] = useState<Draft[]>([]);
  const [saving, setSaving] = useState(false);
  const editId = editing?.variant;
  const itemById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);

  async function load() {
    setLoading(true);
    try {
      setCards(await get<Recipe[]>("/inventory/recipes/"));
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось загрузить тех карты", "bad");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return cards.filter(
      (c) =>
        (!q || c.product_name.toLowerCase().includes(q)) &&
        (!onlyEmpty || c.lines.length === 0)
    );
  }, [cards, query, onlyEmpty]);

  const filled = cards.filter((c) => c.lines.length > 0).length;

  /** Другие объёмы этого же блюда, у которых состав уже заполнен.
   *
   *  Коэффициента между объёмами нет и быть не может: лёд, тапиока и
   *  стакан масштабируются не так, как молоко. Поэтому не считаем, а
   *  копируем — заполнил 0,33, скопировал в 0,5, поправил цифры. */
  function siblings(card: Recipe): Recipe[] {
    return cards.filter(
      (c) => c.product === card.product && c.variant !== card.variant && c.lines.length > 0
    );
  }

  function copyFrom(source: Recipe) {
    setDraft(source.lines.map(toDraft));
    notify(`Состав скопирован из «${source.product_name}»`, "ok");
  }

  function startEdit(card: Recipe, focusLine?: number) {
    setEditing({ variant: card.variant, focusLine });
    setDraft(card.lines.length ? card.lines.map(toDraft) : [emptyLine]);
  }

  function changeLine(index: number, changes: Partial<Draft>) {
    setDraft((lines) => lines.map((line, i) => (i === index ? { ...line, ...changes } : line)));
  }

  async function save(variantId: number) {
    if (saving) return;
    const lines = [];
    for (const [index, line] of draft.entries()) {
      const { item, original } = line;
      const quantity = line.quantity.trim();
      const comment = line.comment.trim();
      if (!original && item === "" && !quantity && !comment) continue;
      if (item === "" || !Number.isFinite(Number(quantity)) || Number(quantity) <= 0) {
        notify(`Строка ${index + 1}. Выберите ингредиент и укажите расход больше нуля.`, "bad");
        return;
      }
      lines.push({ item, quantity, comment });
    }
    setSaving(true);
    try {
      const saved = await put<Recipe>(`/inventory/recipes/${variantId}/`, { lines });
      setCards((cs) => cs.map((c) => (c.variant === variantId ? saved : c)));
      setEditing(null);
      notify(lines.length ? "Тех карта сохранена" : "Тех карта очищена", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Ошибка", "bad");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="stack loose mt-4">
      <div className="wrap" style={{ alignItems: "center" }}>
        <input
          className="input grow"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Найти блюдо"
        />
        <button
          className={"btn sm " + (onlyEmpty ? "" : "ghost")}
          onClick={() => setOnlyEmpty((v) => !v)}
          title="Показать блюда, у которых нет тех карты"
        >
          Без карты
        </button>
      </div>
      <span className="muted sm">
        Карта есть у {filled} из {cards.length} блюд. Расход указывается на одну
        порцию — по нему списывается склад, когда блюдо готово.
      </span>

      {loading &&
        Array.from({ length: 3 }).map((_, i) => (
          <div className="skeleton sm" key={i} />
        ))}

      {shown.map((card) => (
        <div className="card" key={card.variant}>
          <div className="between">
            <div className="row-body">
              <strong className="title">
                {card.product_name}
              </strong>
              <span className="muted sm">
                {card.category_name}
                {card.lines.length === 0
                  ? " · тех карты нет"
                  : Number(card.cost) > 0
                    ? ` · себестоимость ${card.cost_partial ? "от " : ""}${Number(
                        card.cost
                      ).toLocaleString("ru")} ₽ из ${Number(card.price).toLocaleString("ru")} ₽`
                    : " · цены закупки неизвестны"}
              </span>
            </div>
            {editId !== card.variant && (
              <button className="btn sm ghost" onClick={() => startEdit(card)} disabled={saving}>
                <Icon name="edit" size={15} /> {card.lines.length ? "Изменить" : "Составить"}
              </button>
            )}
          </div>

          {/* просмотр */}
          {editId !== card.variant && card.lines.length > 0 && (
            <ul className="stack tight list mt-3">
              {card.lines.map((l, index) => (
                <li key={l.id} className="between">
                  <span>
                    {l.item_name}
                    {l.comment && (
                      <span className="muted sm"> · {l.comment}</span>
                    )}
                  </span>
                  <span className="inline">
                    <span className="num muted">{fmtQty(l.quantity)} {l.unit_display}</span>
                    <button
                      className="icon-btn sm"
                      aria-label={`Изменить ингредиент «${l.item_name}»`}
                      title="Изменить ингредиент"
                      onClick={() => startEdit(card, index)}
                      disabled={saving}
                    >
                      <Icon name="edit" size={14} />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}

          {/* редактор */}
          {editId === card.variant && (
            <fieldset className="stack recipe-editor mt-3" disabled={saving} aria-label="Состав техкарты">
              {draft.map((d, idx) => {
                const item = d.item === "" ? undefined : itemById.get(d.item);
                const unavailable = d.original && !itemById.has(d.original.item) ? d.original : undefined;
                const unit = item?.unit_display ?? (unavailable?.item === d.item ? unavailable?.unit_display : undefined);
                return (
                  <div className="receipt-line" key={idx}>
                    <select
                      className="input"
                      value={d.item}
                      aria-label={`Ингредиент ${idx + 1}`}
                      autoFocus={editing?.focusLine === idx}
                      onChange={(e) => changeLine(idx, {
                        item: e.target.value === "" ? "" : Number(e.target.value),
                      })}
                    >
                      <option value="">— ингредиент —</option>
                      {unavailable && (
                        <option value={unavailable.item}>
                          {unavailable.item_name} ({unavailable.unit_display}) · скрыт со склада
                        </option>
                      )}
                      {items.map((i) => (
                        <option key={i.id} value={i.id}>
                          {i.name} ({i.unit_display})
                        </option>
                      ))}
                    </select>
                    <input
                      className="input"
                      inputMode="decimal"
                      value={d.quantity}
                      aria-label={`Расход на порцию, строка ${idx + 1}`}
                      onChange={(e) => changeLine(idx, { quantity: decimalInput(e.target.value) })}
                      placeholder={unit ?? "кол-во"}
                      title="Расход на одну порцию"
                    />
                    <input
                      className="input"
                      value={d.comment}
                      aria-label={`Заметка, строка ${idx + 1}`}
                      maxLength={200}
                      onChange={(e) => changeLine(idx, { comment: e.target.value })}
                      placeholder="заметка"
                    />
                    <button
                      className="icon-btn danger"
                      onClick={() => setDraft((ds) => ds.filter((_, i) => i !== idx))}
                      aria-label="Убрать строку"
                    >
                      <Icon name="trash" size={16} />
                    </button>
                  </div>
                );
              })}
              <div className="wrap">
                <button
                  className="btn sm ghost"
                  onClick={() => setDraft((ds) => [...ds, emptyLine])}
                >
                  <Icon name="plus" size={15} /> Ингредиент
                </button>
                <button
                  className="btn sm"
                  onClick={() => save(card.variant)}
                  disabled={saving}
                >
                  <Icon name={saving ? "spark" : "check"} size={16} /> Сохранить
                </button>
                <button className="btn sm ghost" onClick={() => setEditing(null)}>
                  Отмена
                </button>
              </div>

              {siblings(card).length > 0 && (
                <div className="wrap" style={{ alignItems: "center" }}>
                  <span className="muted sm">Скопировать состав из:</span>
                  {siblings(card).map((sib) => (
                    <button
                      key={sib.variant}
                      className="btn sm ghost"
                      onClick={() => copyFrom(sib)}
                      title="Подставит состав целиком — количества поправьте под этот объём"
                    >
                      <Icon name="copy" size={14} /> {sib.product_name}
                    </button>
                  ))}
                </div>
              )}
            </fieldset>
          )}
        </div>
      ))}

      {!loading && shown.length === 0 && (
        <div className="card center">
          <p className="muted m-0">Блюда не нашлись.</p>
        </div>
      )}
    </div>
  );
}
