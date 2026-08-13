import { useEffect, useMemo, useState } from "react";
import { get, put, ApiError } from "../../api";
import type { Recipe, StockItem } from "../../types";
import Icon from "../../components/Icon";

type Draft = { item: number | ""; quantity: string; comment: string };

function fmtQty(q: string | number): string {
  return Number(q).toLocaleString("ru", { maximumFractionDigits: 3 });
}

type Props = {
  items: StockItem[];
  notify: (m: string) => void;
};

export default function Recipes({ items, notify }: Props) {
  const [cards, setCards] = useState<Recipe[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [onlyEmpty, setOnlyEmpty] = useState(false);

  // редактирование карты одного блюда
  const [editId, setEditId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft[]>([]);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      setCards(await get<Recipe[]>("/inventory/recipes/"));
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось загрузить тех карты");
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

  function startEdit(card: Recipe) {
    setEditId(card.product);
    setDraft(
      card.lines.length
        ? card.lines.map((l) => ({
            item: l.item,
            quantity: String(Number(l.quantity)),
            comment: l.comment,
          }))
        : [{ item: items[0]?.id ?? "", quantity: "", comment: "" }]
    );
  }

  async function save(productId: number) {
    const lines = draft
      .filter((d) => d.item !== "" && Number(d.quantity) > 0)
      .map((d) => ({
        item: d.item,
        quantity: Number(d.quantity),
        ...(d.comment.trim() ? { comment: d.comment.trim() } : {}),
      }));
    setSaving(true);
    try {
      const saved = await put<Recipe>(`/inventory/recipes/${productId}/`, { lines });
      setCards((cs) => cs.map((c) => (c.product === productId ? saved : c)));
      setEditId(null);
      notify(lines.length ? "Тех карта сохранена" : "Тех карта очищена");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="stack" style={{ gap: 12, marginTop: 14 }}>
      <div className="wrap" style={{ gap: 8, alignItems: "center" }}>
        <input
          className="input"
          style={{ flex: 1, minWidth: 160 }}
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
      <span className="muted" style={{ fontSize: 13 }}>
        Карта есть у {filled} из {cards.length} блюд. Расход указывается на одну
        порцию — по нему списывается склад, когда блюдо готово.
      </span>

      {loading &&
        Array.from({ length: 3 }).map((_, i) => (
          <div className="skeleton" style={{ height: 52 }} key={i} />
        ))}

      {shown.map((card) => (
        <div className="card" key={card.product}>
          <div className="between">
            <div className="stack" style={{ gap: 2, minWidth: 0 }}>
              <strong style={{ fontFamily: "Fredoka", fontSize: 16 }}>
                {card.product_name}
              </strong>
              <span className="muted" style={{ fontSize: 12 }}>
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
            {editId !== card.product && (
              <button className="btn sm ghost" onClick={() => startEdit(card)}>
                <Icon name="edit" size={15} /> {card.lines.length ? "Изменить" : "Составить"}
              </button>
            )}
          </div>

          {/* просмотр */}
          {editId !== card.product && card.lines.length > 0 && (
            <ul
              className="stack"
              style={{ gap: 4, margin: "10px 0 0", listStyle: "none", padding: 0 }}
            >
              {card.lines.map((l) => (
                <li key={l.id} className="between">
                  <span>
                    {l.item_name}
                    {l.comment && (
                      <span className="muted" style={{ fontSize: 12 }}> · {l.comment}</span>
                    )}
                  </span>
                  <span className="num muted">
                    {fmtQty(l.quantity)} {l.unit_display}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {/* редактор */}
          {editId === card.product && (
            <div className="stack" style={{ gap: 8, marginTop: 12 }}>
              {draft.map((d, idx) => {
                const it = items.find((i) => i.id === d.item);
                return (
                  <div className="receipt-line" key={idx}>
                    <select
                      className="input"
                      value={d.item}
                      onChange={(e) =>
                        setDraft((ds) =>
                          ds.map((x, i) =>
                            i === idx ? { ...x, item: Number(e.target.value) } : x
                          )
                        )
                      }
                    >
                      <option value="">— товар —</option>
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
                      onChange={(e) =>
                        setDraft((ds) =>
                          ds.map((x, i) => (i === idx ? { ...x, quantity: e.target.value } : x))
                        )
                      }
                      placeholder={it ? it.unit_display : "кол-во"}
                      title="Расход на одну порцию"
                    />
                    <input
                      className="input"
                      value={d.comment}
                      onChange={(e) =>
                        setDraft((ds) =>
                          ds.map((x, i) => (i === idx ? { ...x, comment: e.target.value } : x))
                        )
                      }
                      placeholder="заметка"
                    />
                    <button
                      className="icon-btn danger"
                      onClick={() => setDraft((ds) => ds.filter((_, i) => i !== idx))}
                      aria-label="Убрать строку"
                    >
                      <Icon name="minus" size={16} />
                    </button>
                  </div>
                );
              })}
              <div className="wrap" style={{ gap: 8 }}>
                <button
                  className="btn sm ghost"
                  onClick={() =>
                    setDraft((ds) => [...ds, { item: "", quantity: "", comment: "" }])
                  }
                >
                  <Icon name="plus" size={15} /> Ингредиент
                </button>
                <button
                  className="btn sm"
                  onClick={() => save(card.product)}
                  disabled={saving}
                >
                  <Icon name={saving ? "spark" : "check"} size={16} /> Сохранить
                </button>
                <button className="btn sm ghost" onClick={() => setEditId(null)}>
                  Отмена
                </button>
              </div>
            </div>
          )}
        </div>
      ))}

      {!loading && shown.length === 0 && (
        <div className="card" style={{ textAlign: "center" }}>
          <p className="muted" style={{ margin: 0 }}>Блюда не нашлись.</p>
        </div>
      )}
    </div>
  );
}
