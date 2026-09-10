import { useEffect, useMemo, useState } from "react";
import { del, get, patch, patchForm, post, postForm, ApiError } from "../../api";
import Icon from "../../components/Icon";
import Modal from "../../components/ui/Modal";
import { useToast } from "../../components/ui/Toast";
import type { Category, Product, Station } from "../../types";

/** Правка меню прямо в панели владельца, без Django-админки.

    Товары сгруппированы по категориям — так же, как их видит гость: владелец
    правит меню в той же структуре, в какой оно продаётся.
*/

const STATION_LABEL: Record<Station, string> = { kitchen: "кухня", bar: "бар" };

type ProductDraft = {
  id?: number;
  category: number | "";
  name: string;
  description: string;
  price: string;
  weight_grams: string;
  prep_minutes: string;
  is_available: boolean;
  is_stopped: boolean;
  sort_order: string;
  image: string | null;
  imageFile?: File | null;
  /** Снять текущую картинку при сохранении. */
  dropImage?: boolean;
};

type CategoryDraft = {
  id?: number;
  name: string;
  station: Station;
  sort_order: string;
  is_active: boolean;
};

const emptyProduct = (category: number | ""): ProductDraft => ({
  category,
  name: "",
  description: "",
  price: "",
  weight_grams: "",
  prep_minutes: "",
  is_available: true,
  is_stopped: false,
  sort_order: "0",
  image: null,
  imageFile: null,
});

const emptyCategory = (): CategoryDraft => ({
  name: "",
  station: "bar",
  sort_order: "0",
  is_active: true,
});

export default function Catalog() {
  const notify = useToast();
  const [cats, setCats] = useState<Category[]>([]);
  const [items, setItems] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [openCats, setOpenCats] = useState<Set<number>>(new Set());
  const [pDraft, setPDraft] = useState<ProductDraft | null>(null);
  const [cDraft, setCDraft] = useState<CategoryDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [delProduct, setDelProduct] = useState<number | null>(null);
  const [delCategory, setDelCategory] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  async function load() {
    try {
      const [c, p] = await Promise.all([
        get<Category[]>("/categories/"),
        get<Product[]>("/products/"),
      ]);
      setCats(c);
      setItems(p);
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось загрузить каталог", "bad");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const byCat = useMemo(() => {
    const m = new Map<number, Product[]>();
    for (const p of items) {
      const list = m.get(p.category) ?? [];
      list.push(p);
      m.set(p.category, list);
    }
    return m;
  }, [items]);

  const toggleCat = (id: number) =>
    setOpenCats((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  // ——— товар ———

  async function saveProduct() {
    if (!pDraft) return;
    if (!pDraft.name.trim()) return notify("Укажите название", "bad");
    if (!pDraft.category) return notify("Выберите категорию", "bad");
    if (!pDraft.price.trim()) return notify("Укажите цену", "bad");
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        category: pDraft.category,
        name: pDraft.name.trim(),
        description: pDraft.description.trim(),
        price: pDraft.price,
        weight_grams: pDraft.weight_grams === "" ? null : pDraft.weight_grams,
        prep_minutes: pDraft.prep_minutes === "" ? null : pDraft.prep_minutes,
        is_available: pDraft.is_available,
        is_stopped: pDraft.is_stopped,
        sort_order: pDraft.sort_order || 0,
      };
      // Картинку шлём только когда её выбрали: multipart на каждое сохранение
      // гонял бы файл заново при правке одной цены.
      if (pDraft.imageFile) {
        const form = new FormData();
        for (const [k, v] of Object.entries(body)) {
          if (v !== null) form.append(k, String(v));
        }
        form.append("image", pDraft.imageFile);
        pDraft.id
          ? await patchForm(`/products/${pDraft.id}/`, form)
          : await postForm("/products/", form);
      } else {
        if (pDraft.dropImage) body.image = null;
        pDraft.id
          ? await patch(`/products/${pDraft.id}/`, body)
          : await post("/products/", body);
      }
      notify(pDraft.id ? "Блюдо сохранено" : "Блюдо добавлено", "ok");
      setPDraft(null);
      await load();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  async function removeProduct(id: number) {
    setBusy(id);
    try {
      await del(`/products/${id}/`);
      notify("Блюдо удалено", "ok");
      setDelProduct(null);
      await load();
    } catch (e) {
      // 409 — товар в заказах; текст с подсказкой приходит с сервера
      notify(e instanceof ApiError ? e.message : "Не удалось удалить", "bad");
    } finally {
      setBusy(null);
    }
  }

  /** Быстрые переключатели прямо в списке — частые действия дня. */
  async function toggleProduct(p: Product, field: "is_available" | "is_stopped") {
    setBusy(p.id);
    try {
      await patch(`/products/${p.id}/`, { [field]: !p[field] });
      await load();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setBusy(null);
    }
  }

  // ——— категория ———

  async function saveCategory() {
    if (!cDraft) return;
    if (!cDraft.name.trim()) return notify("Укажите название", "bad");
    setSaving(true);
    try {
      const body = {
        name: cDraft.name.trim(),
        station: cDraft.station,
        sort_order: cDraft.sort_order || 0,
        is_active: cDraft.is_active,
      };
      cDraft.id
        ? await patch(`/categories/${cDraft.id}/`, body)
        : await post("/categories/", body);
      notify(cDraft.id ? "Категория сохранена" : "Категория добавлена", "ok");
      setCDraft(null);
      await load();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSaving(false);
    }
  }

  async function removeCategory(id: number) {
    setBusy(id);
    try {
      await del(`/categories/${id}/`);
      notify("Категория удалена", "ok");
      setDelCategory(null);
      await load();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось удалить", "bad");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="between">
        <h1 className="h1">Каталог</h1>
        <div className="wrap">
          <button className="btn sm ghost" onClick={() => setCDraft(emptyCategory())}>
            <Icon name="plus" size={15} /> Категория
          </button>
          <button
            className="btn sm"
            disabled={cats.length === 0}
            onClick={() => setPDraft(emptyProduct(cats[0]?.id ?? ""))}
          >
            <Icon name="plus" size={15} /> Блюдо
          </button>
        </div>
      </div>
      <p className="muted subtitle">
        {loading
          ? "Загрузка…"
          : `Меню заведения · ${cats.length} категорий · ${items.length} блюд`}
      </p>

      <div className="card mt-4">
        {!loading && cats.length === 0 && (
          <p className="muted m-0">
            Категорий пока нет. Начните с категории — «Кофе», «Завтраки», — а
            потом добавьте в неё блюда.
          </p>
        )}

        <div className="stack">
          {cats.map((c, i) => {
            const list = byCat.get(c.id) ?? [];
            const open = openCats.has(c.id);
            return (
              // разделитель между категориями, но не над первой
              <div key={c.id} className={i > 0 ? "rule-top" : undefined}>
                <div className="row mt-2">
                  <button
                    className="icon-btn"
                    onClick={() => toggleCat(c.id)}
                    aria-label={open ? "Свернуть" : "Развернуть"}
                  >
                    <span className={open ? "" : "rot--90"}>
                      <Icon name="arrowDown" size={16} />
                    </span>
                  </button>
                  <div className="row-body">
                    <strong>
                      {c.name}
                      {!c.is_active && <span className="badge mini ml-2">скрыта</span>}
                    </strong>
                    <span className="muted">
                      {STATION_LABEL[c.station]} · {list.length} блюд
                    </span>
                  </div>
                  <button
                    className="icon-btn"
                    aria-label="Изменить категорию"
                    title="Изменить категорию"
                    onClick={() =>
                      setCDraft({
                        id: c.id,
                        name: c.name,
                        station: c.station,
                        sort_order: String(c.sort_order),
                        is_active: c.is_active,
                      })
                    }
                  >
                    <Icon name="edit" size={16} />
                  </button>
                  <button
                    className="icon-btn danger"
                    aria-label="Удалить категорию"
                    title="Удалить категорию"
                    onClick={() => { setDelCategory(c.id); setDelProduct(null); }}
                  >
                    <Icon name="trash" size={16} />
                  </button>
                </div>

                {delCategory === c.id && (
                  <div className="row indent">
                    <span className="muted" style={{ flex: 1, minWidth: 0 }}>
                      Удалить категорию «{c.name}»?
                    </span>
                    <button className="btn sm ghost" onClick={() => setDelCategory(null)}>
                      Отмена
                    </button>
                    <button
                      className="btn sm danger"
                      disabled={busy === c.id}
                      onClick={() => removeCategory(c.id)}
                    >
                      <Icon name="trash" size={15} /> Удалить
                    </button>
                  </div>
                )}

                {open && (
                  <div className="stack tight mb-2">
                    {list.length === 0 && (
                      <p className="muted sm indent m-0">В категории пока нет блюд.</p>
                    )}
                    {list.map((p) => (
                      <div key={p.id}>
                        <div className="row indent">
                          {p.thumbnail ? (
                            <img
                              src={p.thumbnail}
                              alt=""
                              style={{
                                width: 34, height: 34, borderRadius: 8,
                                objectFit: "cover", flexShrink: 0,
                              }}
                            />
                          ) : (
                            <span className="tx-icon"><Icon name="bowl" size={16} /></span>
                          )}
                          <div className="row-body">
                            <strong>
                              {p.name}
                              {!p.is_available && (
                                <span className="badge mini ml-2">не в меню</span>
                              )}
                              {p.is_stopped && (
                                <span className="badge mini ml-2">стоп</span>
                              )}
                            </strong>
                            <span className="muted">
                              {Number(p.price).toLocaleString("ru")} ₽
                              {p.weight_grams ? ` · ${p.weight_grams} г` : ""}
                            </span>
                          </div>
                          <button
                            className={"btn sm" + (p.is_stopped ? " danger" : " ghost")}
                            disabled={busy === p.id}
                            title="Временно закончилось"
                            onClick={() => toggleProduct(p, "is_stopped")}
                          >
                            стоп
                          </button>
                          <button
                            className="icon-btn"
                            aria-label="Изменить блюдо"
                            title="Изменить блюдо"
                            onClick={() =>
                              setPDraft({
                                id: p.id,
                                category: p.category,
                                name: p.name,
                                description: p.description,
                                price: String(p.price),
                                weight_grams: p.weight_grams ? String(p.weight_grams) : "",
                                prep_minutes: p.prep_minutes ? String(p.prep_minutes) : "",
                                is_available: p.is_available,
                                is_stopped: p.is_stopped,
                                sort_order: String(p.sort_order),
                                image: p.image,
                                imageFile: null,
                              })
                            }
                          >
                            <Icon name="edit" size={16} />
                          </button>
                          <button
                            className="icon-btn danger"
                            aria-label="Удалить блюдо"
                            title="Удалить блюдо"
                            onClick={() => { setDelProduct(p.id); setDelCategory(null); }}
                          >
                            <Icon name="trash" size={16} />
                          </button>
                        </div>

                        {delProduct === p.id && (
                          <div className="row indent">
                            <span className="muted" style={{ flex: 1, minWidth: 0 }}>
                              Удалить «{p.name}» из меню?
                            </span>
                            <button className="btn sm ghost" onClick={() => setDelProduct(null)}>
                              Отмена
                            </button>
                            <button
                              className="btn sm danger"
                              disabled={busy === p.id}
                              onClick={() => removeProduct(p.id)}
                            >
                              <Icon name="trash" size={15} /> Удалить
                            </button>
                          </div>
                        )}
                      </div>
                    ))}
                    <button
                      className="btn sm ghost indent"
                      style={{ alignSelf: "flex-start" }}
                      onClick={() => setPDraft(emptyProduct(c.id))}
                    >
                      <Icon name="plus" size={15} /> Блюдо в «{c.name}»
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {pDraft && (
        <ProductForm
          draft={pDraft}
          cats={cats}
          saving={saving}
          onChange={setPDraft}
          onClose={() => setPDraft(null)}
          onSave={saveProduct}
        />
      )}

      {cDraft && (
        <CategoryForm
          draft={cDraft}
          saving={saving}
          onChange={setCDraft}
          onClose={() => setCDraft(null)}
          onSave={saveCategory}
        />
      )}
    </>
  );
}

function ProductForm({
  draft, cats, saving, onChange, onClose, onSave,
}: {
  draft: ProductDraft;
  cats: Category[];
  saving: boolean;
  onChange: (d: ProductDraft) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const set = <K extends keyof ProductDraft>(k: K, v: ProductDraft[K]) =>
    onChange({ ...draft, [k]: v });
  // выбранный файл показываем сразу, до сохранения
  const preview = draft.imageFile ? URL.createObjectURL(draft.imageFile) : draft.image;

  return (
    <Modal
      onClose={onClose}
      head={
        <div className="between">
          <strong className="title lg">{draft.id ? "Блюдо" : "Новое блюдо"}</strong>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" size={18} />
          </button>
        </div>
      }
    >
      <label className="field">
        <span className="label">Название</span>
        <input
          className="input"
          value={draft.name}
          onChange={(e) => set("name", e.target.value)}
          placeholder="Латте"
          maxLength={200}
          autoFocus
        />
      </label>

      <div className="grid cols-2">
        <label className="field">
          <span className="label">Категория</span>
          <select
            className="input"
            value={draft.category}
            onChange={(e) => set("category", Number(e.target.value))}
          >
            {cats.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="label">Цена, ₽</span>
          <input
            className="input"
            value={draft.price}
            onChange={(e) => set("price", e.target.value.replace(",", "."))}
            inputMode="decimal"
            placeholder="240"
          />
        </label>
      </div>

      <label className="field">
        <span className="label">Описание</span>
        <textarea
          className="input"
          rows={2}
          value={draft.description}
          onChange={(e) => set("description", e.target.value)}
          placeholder="Что внутри — гость читает это в меню"
        />
      </label>

      <div className="grid cols-2">
        <label className="field">
          <span className="label">Вес, г</span>
          <input
            className="input"
            value={draft.weight_grams}
            onChange={(e) => set("weight_grams", e.target.value.replace(/\D/g, ""))}
            inputMode="numeric"
            placeholder="—"
          />
        </label>
        <label className="field">
          <span className="label">Готовится, мин</span>
          <input
            className="input"
            value={draft.prep_minutes}
            onChange={(e) => set("prep_minutes", e.target.value.replace(/\D/g, ""))}
            inputMode="numeric"
            placeholder="—"
          />
        </label>
      </div>

      <div className="field">
        <span className="label">Фото</span>
        <div className="wrap" style={{ alignItems: "center" }}>
          {preview && (
            <img
              src={preview}
              alt=""
              style={{ width: 56, height: 56, borderRadius: 10, objectFit: "cover" }}
            />
          )}
          <input
            type="file"
            accept="image/*"
            className="input grow"
            onChange={(e) =>
              onChange({
                ...draft,
                imageFile: e.target.files?.[0] ?? null,
                dropImage: false,
              })
            }
          />
          {preview && (
            <button
              className="icon-btn danger"
              aria-label="Убрать фото"
              title="Убрать фото"
              onClick={() =>
                onChange({ ...draft, imageFile: null, image: null, dropImage: true })
              }
            >
              <Icon name="trash" size={16} />
            </button>
          )}
        </div>
      </div>

      <div className="wrap mt-2">
        <button
          className={"btn sm" + (draft.is_available ? "" : " ghost")}
          onClick={() => set("is_available", !draft.is_available)}
        >
          <Icon name={draft.is_available ? "check" : "close"} size={15} /> В меню
        </button>
        <button
          className={"btn sm" + (draft.is_stopped ? " danger" : " ghost")}
          onClick={() => set("is_stopped", !draft.is_stopped)}
        >
          <Icon name={draft.is_stopped ? "check" : "close"} size={15} /> На стопе
        </button>
      </div>
      <p className="muted sm mt-2">
        «В меню» — показывать гостю. «На стопе» — видно, но заказать нельзя:
        временно закончилось.
      </p>

      <button className="btn block mt-3" disabled={saving} onClick={onSave}>
        <Icon name="check" size={17} /> Сохранить
      </button>
    </Modal>
  );
}

function CategoryForm({
  draft, saving, onChange, onClose, onSave,
}: {
  draft: CategoryDraft;
  saving: boolean;
  onChange: (d: CategoryDraft) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const set = <K extends keyof CategoryDraft>(k: K, v: CategoryDraft[K]) =>
    onChange({ ...draft, [k]: v });

  return (
    <Modal
      onClose={onClose}
      head={
        <div className="between">
          <strong className="title lg">
            {draft.id ? "Категория" : "Новая категория"}
          </strong>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" size={18} />
          </button>
        </div>
      }
    >
      <label className="field">
        <span className="label">Название</span>
        <input
          className="input"
          value={draft.name}
          onChange={(e) => set("name", e.target.value)}
          placeholder="Завтраки"
          maxLength={100}
          autoFocus
        />
      </label>

      <div className="field">
        <span className="label">Куда уходят заказы</span>
        <div className="wrap">
          {(["kitchen", "bar"] as const).map((st) => (
            <button
              key={st}
              className={"btn sm" + (draft.station === st ? "" : " ghost")}
              onClick={() => set("station", st)}
            >
              <Icon name={st === "kitchen" ? "sandwich" : "coffee"} size={15} />
              {st === "kitchen" ? "Кухня" : "Бар"}
            </button>
          ))}
        </div>
        <p className="muted sm mt-2 m-0">
          На чей экран попадут блюда этой категории.
        </p>
      </div>

      <div className="grid cols-2">
        <label className="field">
          <span className="label">Порядок</span>
          <input
            className="input"
            value={draft.sort_order}
            onChange={(e) => set("sort_order", e.target.value.replace(/\D/g, ""))}
            inputMode="numeric"
          />
        </label>
        <div className="field">
          <span className="label">Показывать</span>
          <button
            className={"btn sm" + (draft.is_active ? "" : " ghost")}
            onClick={() => set("is_active", !draft.is_active)}
          >
            <Icon name={draft.is_active ? "check" : "close"} size={15} />
            {draft.is_active ? "В меню" : "Скрыта"}
          </button>
        </div>
      </div>

      <button className="btn block mt-3" disabled={saving} onClick={onSave}>
        <Icon name="check" size={17} /> Сохранить
      </button>
    </Modal>
  );
}
