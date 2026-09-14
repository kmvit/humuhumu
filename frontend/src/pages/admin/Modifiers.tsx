/** Наборы опций: «Молоко», «Добавки», «Сироп».
 *
 *  Набор общий и цепляется к блюдам списком — «Молоко» заводится один раз
 *  на все кофе. Внутри опции, у каждой — что она делает со складом.
 */
import { useEffect, useMemo, useState } from "react";
import { del, get, patch, post, ApiError } from "../../api";
import { decimalInput } from "../../decimal";
import Icon from "../../components/Icon";
import Modal from "../../components/ui/Modal";
import { useToast } from "../../components/ui/Toast";
import type { Product, StockItem } from "../../types";

type EffectKind = "add" | "remove" | "swap";

type EffectDraft = {
  kind: EffectKind;
  item: number | "";
  replacement: number | "";
  quantity: string;
};

type ModifierDraft = {
  id?: number;
  name: string;
  price_delta: string;
  is_stopped: boolean;
  effects: EffectDraft[];
};

type GroupDraft = {
  id?: number;
  name: string;
  min_choices: string;
  max_choices: string;
  products: number[];
  modifiers: ModifierDraft[];
};

/** Что приходит с сервера. */
type Group = {
  id: number;
  name: string;
  min_choices: number;
  max_choices: number;
  is_required: boolean;
  products: number[];
  modifiers: {
    id: number;
    name: string;
    price_delta: string;
    is_stopped: boolean;
    effects: {
      id: number;
      kind: EffectKind;
      item: number;
      replacement: number | null;
      quantity: string | null;
    }[];
  }[];
};

const KINDS: { value: EffectKind; label: string; hint: string }[] = [
  { value: "add", label: "Добавить", hint: "плюс к расходу: «+ шот эспрессо»" },
  { value: "remove", label: "Убрать", hint: "не класть вовсе: «без сиропа»" },
  { value: "swap", label: "Заменить", hint: "в том же количестве: «на овсяном»" },
];

const emptyEffect = (): EffectDraft => ({
  kind: "add", item: "", replacement: "", quantity: "",
});

const emptyModifier = (): ModifierDraft => ({
  name: "", price_delta: "", is_stopped: false, effects: [],
});

const emptyGroup = (): GroupDraft => ({
  name: "", min_choices: "0", max_choices: "1", products: [], modifiers: [emptyModifier()],
});

const draftFrom = (g: Group): GroupDraft => ({
  id: g.id,
  name: g.name,
  min_choices: String(g.min_choices),
  max_choices: String(g.max_choices),
  products: [...g.products],
  modifiers: g.modifiers.map((m) => ({
    id: m.id,
    name: m.name,
    price_delta: String(Number(m.price_delta)),
    is_stopped: m.is_stopped,
    effects: m.effects.map((e) => ({
      kind: e.kind,
      item: e.item,
      replacement: e.replacement ?? "",
      quantity: e.quantity ? String(Number(e.quantity)) : "",
    })),
  })),
});

export default function Modifiers() {
  const notify = useToast();
  const [groups, setGroups] = useState<Group[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [items, setItems] = useState<StockItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<GroupDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [delId, setDelId] = useState<number | null>(null);

  async function load() {
    try {
      const [g, p, i] = await Promise.all([
        get<Group[]>("/modifier-groups/"),
        get<Product[]>("/products/"),
        get<StockItem[]>("/inventory/items/").catch(() => [] as StockItem[]),
      ]);
      setGroups(g);
      setProducts(p);
      setItems(i);
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось загрузить", "bad");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const productName = useMemo(
    () => new Map(products.map((p) => [p.id, p.name])),
    [products]
  );

  async function save() {
    if (!draft) return;
    if (!draft.name.trim()) return notify("Укажите название набора", "bad");
    const rows = draft.modifiers.filter((m) => m.name.trim());
    if (!rows.length) return notify("Добавьте хотя бы одну опцию", "bad");

    setSaving(true);
    try {
      const body = {
        name: draft.name.trim(),
        min_choices: Number(draft.min_choices) || 0,
        max_choices: Number(draft.max_choices) || 1,
        products: draft.products,
        modifiers: rows.map((m) => ({
          ...(m.id ? { id: m.id } : {}),
          name: m.name.trim(),
          price_delta: m.price_delta || "0",
          is_stopped: m.is_stopped,
          effects: m.effects
            .filter((e) => e.item !== "")
            .map((e) => ({
              kind: e.kind,
              item: e.item,
              ...(e.kind === "swap" ? { replacement: e.replacement } : {}),
              ...(e.kind === "add" ? { quantity: e.quantity || "0" } : {}),
            })),
        })),
      };
      draft.id
        ? await patch(`/modifier-groups/${draft.id}/`, body)
        : await post("/modifier-groups/", body);
      notify(draft.id ? "Набор сохранён" : "Набор добавлен", "ok");
      setDraft(null);
      await load();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: number) {
    try {
      await del(`/modifier-groups/${id}/`);
      notify("Набор удалён", "ok");
      setDelId(null);
      await load();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось удалить", "bad");
    }
  }

  const choiceText = (g: Group) =>
    g.min_choices && g.max_choices === 1
      ? "выбрать одно"
      : g.max_choices === 1
        ? "не больше одного"
        : `до ${g.max_choices}`;

  return (
    <div className="stack loose mt-4">
      <div className="between">
        <span className="muted sm">
          Опции набираются поверх выбранного объёма: «на овсяном», «+ шот».
          Набор цепляется сразу к нескольким блюдам.
        </span>
        <button className="btn sm" onClick={() => setDraft(emptyGroup())}>
          <Icon name="plus" size={15} /> Набор
        </button>
      </div>

      {loading && <div className="skeleton sm" />}

      {!loading && groups.length === 0 && (
        <div className="card center">
          <p className="muted m-0">Наборов опций пока нет.</p>
        </div>
      )}

      {groups.map((g) => (
        <div className="card" key={g.id}>
          <div className="between">
            <div className="row-body">
              <strong className="title">{g.name}</strong>
              <span className="muted sm">
                {choiceText(g)}
                {g.is_required && " · обязательный"}
                {" · "}
                {g.products.length
                  ? g.products.map((id) => productName.get(id) ?? "?").join(", ")
                  : "ни к одному блюду не привязан"}
              </span>
            </div>
            <span className="inline tight">
              <button
                className="icon-btn"
                aria-label="Изменить набор"
                onClick={() => setDraft(draftFrom(g))}
              >
                <Icon name="edit" size={16} />
              </button>
              {delId === g.id ? (
                <>
                  <span className="muted sm">Удалить?</span>
                  <button className="icon-btn danger" onClick={() => remove(g.id)}>
                    <Icon name="check" size={16} />
                  </button>
                  <button className="icon-btn" onClick={() => setDelId(null)}>
                    <Icon name="close" size={16} />
                  </button>
                </>
              ) : (
                <button
                  className="icon-btn danger"
                  aria-label="Удалить набор"
                  onClick={() => setDelId(g.id)}
                >
                  <Icon name="trash" size={16} />
                </button>
              )}
            </span>
          </div>
          <ul className="stack tight list mt-3">
            {g.modifiers.map((m) => (
              <li key={m.id} className="between">
                <span>
                  {m.name}
                  {m.is_stopped && <span className="badge mini ml-2">стоп</span>}
                  {m.effects.length === 0 && (
                    <span className="muted sm"> · склад не тронет</span>
                  )}
                </span>
                <span className="num muted">
                  {Number(m.price_delta) > 0 && "+"}
                  {Number(m.price_delta).toLocaleString("ru")} ₽
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}

      {draft && (
        <GroupForm
          draft={draft}
          products={products}
          items={items}
          saving={saving}
          onChange={setDraft}
          onClose={() => setDraft(null)}
          onSave={save}
        />
      )}
    </div>
  );
}

function GroupForm({
  draft, products, items, saving, onChange, onClose, onSave,
}: {
  draft: GroupDraft;
  products: Product[];
  items: StockItem[];
  saving: boolean;
  onChange: (d: GroupDraft) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const set = <K extends keyof GroupDraft>(k: K, v: GroupDraft[K]) =>
    onChange({ ...draft, [k]: v });

  const setMod = (idx: number, patch: Partial<ModifierDraft>) =>
    set("modifiers", draft.modifiers.map((m, i) => (i === idx ? { ...m, ...patch } : m)));

  const setEffect = (mi: number, ei: number, patch: Partial<EffectDraft>) =>
    setMod(mi, {
      effects: draft.modifiers[mi].effects.map((e, i) =>
        i === ei ? { ...e, ...patch } : e
      ),
    });

  const toggleProduct = (id: number) =>
    set(
      "products",
      draft.products.includes(id)
        ? draft.products.filter((x) => x !== id)
        : [...draft.products, id]
    );

  return (
    <Modal
      onClose={onClose}
      head={
        <div className="between">
          <strong className="title lg">{draft.id ? "Набор опций" : "Новый набор"}</strong>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" size={18} />
          </button>
        </div>
      }
    >
      <div className="stack loose">
        <label className="field">
          <span className="label">Название</span>
          <input
            className="input"
            value={draft.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="напр. Молоко"
          />
        </label>

        <div className="grid cols-2">
          <label className="field">
            <span className="label">Минимум выбрать</span>
            <input
              className="input"
              inputMode="numeric"
              value={draft.min_choices}
              onChange={(e) => set("min_choices", e.target.value.replace(/\D/g, ""))}
            />
            <span className="muted sm">1 — гость обязан выбрать</span>
          </label>
          <label className="field">
            <span className="label">Максимум выбрать</span>
            <input
              className="input"
              inputMode="numeric"
              value={draft.max_choices}
              onChange={(e) => set("max_choices", e.target.value.replace(/\D/g, ""))}
            />
            <span className="muted sm">1 — только одно из набора</span>
          </label>
        </div>

        <div className="field">
          <span className="label">К каким блюдам</span>
          <div className="wrap">
            {products.map((p) => (
              <button
                key={p.id}
                className={
                  "size-chip" + (draft.products.includes(p.id) ? " active" : "")
                }
                aria-pressed={draft.products.includes(p.id)}
                onClick={() => toggleProduct(p.id)}
              >
                {p.name}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <span className="label">Опции</span>
          <div className="stack">
            {draft.modifiers.map((m, mi) => (
              <div className="card sub" key={m.id ?? `new-${mi}`}>
                <div className="variant-row">
                  <input
                    className="input"
                    value={m.name}
                    onChange={(e) => setMod(mi, { name: e.target.value })}
                    placeholder="название, напр. Овсяное"
                  />
                  <input
                    className="input"
                    inputMode="decimal"
                    value={m.price_delta}
                    onChange={(e) =>
                      setMod(mi, { price_delta: decimalInput(e.target.value) })
                    }
                    placeholder="надбавка, ₽"
                  />
                  <button
                    className={"btn sm" + (m.is_stopped ? " danger" : " ghost")}
                    onClick={() => setMod(mi, { is_stopped: !m.is_stopped })}
                  >
                    стоп
                  </button>
                  <button
                    className="icon-btn danger"
                    aria-label="Убрать опцию"
                    onClick={() =>
                      set("modifiers", draft.modifiers.filter((_, i) => i !== mi))
                    }
                  >
                    <Icon name="trash" size={16} />
                  </button>
                </div>

                {/* что опция делает со складом */}
                <div className="stack tight mt-2">
                  {m.effects.map((e, ei) => (
                    <div className="effect-row" key={ei}>
                      <select
                        className="input"
                        value={e.kind}
                        onChange={(ev) =>
                          setEffect(mi, ei, { kind: ev.target.value as EffectKind })
                        }
                      >
                        {KINDS.map((k) => (
                          <option key={k.value} value={k.value}>{k.label}</option>
                        ))}
                      </select>
                      <select
                        className="input"
                        value={e.item}
                        onChange={(ev) =>
                          setEffect(mi, ei, { item: Number(ev.target.value) })
                        }
                      >
                        <option value="">— товар склада —</option>
                        {items.map((it) => (
                          <option key={it.id} value={it.id}>
                            {it.name} ({it.unit_display})
                          </option>
                        ))}
                      </select>
                      {e.kind === "swap" && (
                        <select
                          className="input"
                          value={e.replacement}
                          onChange={(ev) =>
                            setEffect(mi, ei, { replacement: Number(ev.target.value) })
                          }
                        >
                          <option value="">— на что —</option>
                          {items.map((it) => (
                            <option key={it.id} value={it.id}>
                              {it.name} ({it.unit_display})
                            </option>
                          ))}
                        </select>
                      )}
                      {e.kind === "add" && (
                        <input
                          className="input"
                          inputMode="decimal"
                          value={e.quantity}
                          onChange={(ev) =>
                            setEffect(mi, ei, { quantity: decimalInput(ev.target.value) })
                          }
                          placeholder="сколько"
                        />
                      )}
                      <button
                        className="icon-btn danger"
                        aria-label="Убрать действие"
                        onClick={() =>
                          setMod(mi, {
                            effects: m.effects.filter((_, i) => i !== ei),
                          })
                        }
                      >
                        <Icon name="trash" size={16} />
                      </button>
                    </div>
                  ))}
                  <button
                    className="btn sm ghost"
                    onClick={() => setMod(mi, { effects: [...m.effects, emptyEffect()] })}
                  >
                    <Icon name="plus" size={14} /> Действие со складом
                  </button>
                  {m.effects.length === 0 && (
                    <span className="muted sm">
                      Без действий опция меняет только цену — склад не тронет.
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
          <button
            className="btn sm ghost mt-2"
            onClick={() => set("modifiers", [...draft.modifiers, emptyModifier()])}
          >
            <Icon name="plus" size={15} /> Опция
          </button>
        </div>

        <p className="muted sm m-0">
          «Убрать» и «Заменить» берут количество из тех карты проданного объёма —
          одна опция верна и для 0,33, и для 0,7.
        </p>

        <button className="btn block" disabled={saving} onClick={onSave}>
          <Icon name="check" size={17} /> Сохранить
        </button>
      </div>
    </Modal>
  );
}
