import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { get, post, postForm, ApiError } from "../../api";
import type {
  StockCategory,
  StockItem,
  Receipt,
  ReceiptScan,
  StockUnit,
} from "../../types";
import Icon from "../../components/Icon";
import { fmtDateTime } from "../../time";

const UNITS: { value: StockUnit; label: string }[] = [
  { value: "g", label: "г" },
  { value: "ml", label: "мл" },
  { value: "pcs", label: "шт" },
];

// «5500.000» → «5 500», «1.500» → «1,5»
function fmtQty(q: string | number | null): string {
  if (q === null || q === "") return "0";
  return Number(q).toLocaleString("ru", { maximumFractionDigits: 3 });
}

type Line = {
  item: number | "";
  quantity: string;
  unit_cost: string;
  // подсказки от распознавания чека (только для черновика по фото)
  hint?: string; // как позиция называется в чеке
  warn?: boolean; // не удалось уверенно сопоставить/сконвертировать
};

export default function Warehouse() {
  const [cats, setCats] = useState<StockCategory[]>([]);
  const [items, setItems] = useState<StockItem[]>([]);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"stock" | "receipts">("stock");
  const [toast, setToast] = useState<string | null>(null);

  // приход
  const [receiptOpen, setReceiptOpen] = useState(false);
  const [supplier, setSupplier] = useState("");
  const [comment, setComment] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [submitting, setSubmitting] = useState(false);

  // приход по фото чека
  const [scanId, setScanId] = useState<number | null>(null); // если задан — форма = черновик по фото
  const [scanning, setScanning] = useState(false); // идёт загрузка/распознавание
  const fileRef = useRef<HTMLInputElement>(null);

  // новая позиция
  const [niOpen, setNiOpen] = useState(false);
  const [niName, setNiName] = useState("");
  const [niCat, setNiCat] = useState<number | "">("");
  const [niUnit, setNiUnit] = useState<StockUnit>("pcs");
  const [niMin, setNiMin] = useState("");

  // новая категория
  const [catOpen, setCatOpen] = useState(false);
  const [newCatName, setNewCatName] = useState("");

  // корректировка
  const [adjustId, setAdjustId] = useState<number | null>(null);
  const [adjustQty, setAdjustQty] = useState("");

  function notify(m: string) {
    setToast(m);
    setTimeout(() => setToast(null), 2600);
  }

  async function load() {
    const [c, i, r] = await Promise.all([
      get<StockCategory[]>("/inventory/categories/"),
      get<StockItem[]>("/inventory/items/"),
      get<Receipt[]>("/inventory/receipts/"),
    ]);
    setCats(c);
    setItems(i);
    setReceipts(r);
  }
  useEffect(() => {
    load()
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const activeCats = useMemo(() => cats.filter((c) => c.is_active), [cats]);
  const lowCount = useMemo(() => items.filter((i) => i.is_low && i.is_active).length, [items]);
  const itemById = useMemo(
    () => Object.fromEntries(items.map((i) => [i.id, i])),
    [items]
  );

  // ——— приход ———
  function addLine() {
    setLines((l) => [...l, { item: items[0]?.id ?? "", quantity: "", unit_cost: "" }]);
  }
  function setLine(idx: number, patch: Partial<Line>) {
    setLines((l) => l.map((x, i) => (i === idx ? { ...x, ...patch } : x)));
  }
  function removeLine(idx: number) {
    setLines((l) => l.filter((_, i) => i !== idx));
  }

  function openReceipt() {
    setScanId(null);
    setSupplier("");
    setComment("");
    setLines(items.length ? [{ item: items[0].id, quantity: "", unit_cost: "" }] : []);
    setReceiptOpen(true);
  }

  // ——— приход по фото чека ———
  // Превращаем распознанный черновик в строки формы: подставляем найденную
  // позицию и количество в базовой единице, помечаем неуверенные строки.
  function draftFromScan(scan: ReceiptScan) {
    const p = scan.parsed;
    if (!p) return;
    setScanId(scan.id);
    setSupplier(p.supplier || "");
    setComment("");
    setLines(
      p.lines.map((l) => {
        const matched = l.matched_item_id != null && !!itemById[l.matched_item_id];
        const qty = l.unit_ok && l.base_quantity != null ? l.base_quantity : l.raw_quantity;
        return {
          item: matched ? (l.matched_item_id as number) : "",
          quantity: qty != null ? String(qty) : "",
          unit_cost: l.unit_cost != null ? String(l.unit_cost) : "",
          hint: `${l.raw_name}${l.raw_quantity != null ? ` · ${l.raw_quantity} ${l.raw_unit}` : ""}`,
          warn: !matched || !l.unit_ok,
        };
      })
    );
    setReceiptOpen(true);
  }

  async function pollScan(id: number) {
    // Задача распознавания фоновая — опрашиваем статус до готовности.
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const scan = await get<ReceiptScan>(`/inventory/receipt-scans/${id}/`);
      if (scan.status === "parsed") return scan;
      if (scan.status === "failed") throw new ApiError(0, scan.error || "Не удалось распознать чек");
    }
    throw new ApiError(0, "Распознавание заняло слишком долго");
  }

  async function onPickPhoto(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // чтобы повторный выбор того же файла срабатывал
    if (!file) return;
    if (!items.length) {
      notify("Сначала заведите позиции склада");
      return;
    }
    setScanning(true);
    try {
      const form = new FormData();
      form.append("image", file);
      const created = await postForm<ReceiptScan>("/inventory/receipt-scans/", form);
      // Синхронный режим: ответ уже распознан. Асинхронный: опрашиваем статус.
      if (created.status === "failed") throw new ApiError(0, created.error || "Не удалось распознать чек");
      const parsed = created.status === "parsed" ? created : await pollScan(created.id);
      draftFromScan(parsed);
      notify("Чек распознан — проверьте позиции");
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Ошибка распознавания");
    } finally {
      setScanning(false);
    }
  }

  async function submitReceipt() {
    const payloadItems = lines
      .filter((l) => l.item !== "" && Number(l.quantity) > 0)
      .map((l) => ({
        item: l.item,
        quantity: Number(l.quantity),
        ...(l.unit_cost.trim() ? { unit_cost: Number(l.unit_cost) } : {}),
      }));
    if (!payloadItems.length) {
      notify("Добавьте хотя бы одну позицию с количеством");
      return;
    }
    setSubmitting(true);
    try {
      const body = {
        supplier: supplier.trim(),
        comment: comment.trim(),
        items: payloadItems,
      };
      // Черновик по фото подтверждаем через confirm (свяжет скан с приходом),
      // ручной приход — обычным способом.
      await post(
        scanId ? `/inventory/receipt-scans/${scanId}/confirm/` : "/inventory/receipts/",
        body
      );
      await load();
      setReceiptOpen(false);
      setScanId(null);
      notify("Приход оприходован");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setSubmitting(false);
    }
  }

  // ——— новая позиция / категория ———
  async function createItem() {
    if (!niName.trim() || niCat === "") {
      notify("Укажите название и категорию");
      return;
    }
    try {
      await post("/inventory/items/", {
        name: niName.trim(),
        category: niCat,
        unit: niUnit,
        min_quantity: niMin.trim() ? Number(niMin) : null,
      });
      await load();
      setNiName("");
      setNiMin("");
      setNiOpen(false);
      notify("Позиция добавлена");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Ошибка");
    }
  }

  async function createCategory() {
    if (!newCatName.trim()) return;
    try {
      await post("/inventory/categories/", {
        name: newCatName.trim(),
        sort_order: cats.length + 1,
      });
      await load();
      setNewCatName("");
      setCatOpen(false);
      notify("Категория добавлена");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Ошибка");
    }
  }

  // ——— корректировка ———
  function startAdjust(it: StockItem) {
    setAdjustId(it.id);
    setAdjustQty(String(Number(it.quantity)));
  }
  async function saveAdjust(id: number) {
    try {
      await post(`/inventory/items/${id}/adjust/`, {
        quantity: Number(adjustQty || 0),
        comment: "инвентаризация",
      });
      await load();
      setAdjustId(null);
      notify("Остаток скорректирован");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Ошибка");
    }
  }

  if (loading)
    return (
      <>
        <h1 className="h1">Склад</h1>
        <div className="stack" style={{ gap: 12, marginTop: 20 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div className="skeleton" style={{ height: 52 }} key={i} />
          ))}
        </div>
      </>
    );

  return (
    <>
      <div className="between" style={{ alignItems: "flex-end" }}>
        <div>
          <h1 className="h1">Склад</h1>
          <p className="muted" style={{ marginTop: 4 }}>
            {items.filter((i) => i.is_active).length} позиций
            {lowCount > 0 && (
              <>
                {" · "}
                <span style={{ color: "var(--danger)" }}>{lowCount} заканчивается</span>
              </>
            )}
          </p>
        </div>
        <div className="wrap" style={{ gap: 8 }}>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            capture="environment"
            onChange={onPickPhoto}
            style={{ display: "none" }}
          />
          <button
            className="btn ghost"
            onClick={() => fileRef.current?.click()}
            disabled={!items.length || scanning}
            title="Распознать позиции с фото чека"
          >
            <Icon name={scanning ? "spark" : "receipt"} size={18} />{" "}
            {scanning ? "Распознаю…" : "Фото чека"}
          </button>
          <button className="btn" onClick={openReceipt} disabled={!items.length}>
            <Icon name="truck" size={18} /> Оприходовать
          </button>
        </div>
      </div>

      {/* вкладки */}
      <div className="wrap" style={{ gap: 8, margin: "18px 0 6px" }}>
        <button
          className={"navlink" + (tab === "stock" ? " active" : "")}
          onClick={() => setTab("stock")}
        >
          <Icon name="box" size={16} /> Остатки
        </button>
        <button
          className={"navlink" + (tab === "receipts" ? " active" : "")}
          onClick={() => setTab("receipts")}
        >
          <Icon name="receipt" size={16} /> Приходы
        </button>
      </div>

      {/* ——— ПРИХОД (форма) ——— */}
      {receiptOpen && (
        <div className="card enter" style={{ marginTop: 12 }}>
          <div className="between">
            <strong style={{ fontFamily: "Fredoka", fontSize: 18 }}>
              {scanId ? "Приход по фото" : "Новый приход"}
            </strong>
            <button className="btn sm ghost" onClick={() => setReceiptOpen(false)}>
              Отмена
            </button>
          </div>
          {scanId != null && (
            <p className="muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
              <Icon name="spark" size={14} /> Распознано с фото. Проверьте позиции и
              количества — строки с
              <span style={{ color: "var(--danger)" }}> оранжевой меткой</span> нужно
              сопоставить или проверить единицы вручную.
            </p>
          )}
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 12 }}>
            <label className="field">
              <span className="label">Поставщик</span>
              <input className="input" value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="напр. Метро" />
            </label>
            <label className="field">
              <span className="label">Комментарий</span>
              <input className="input" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="необязательно" />
            </label>
          </div>

          <div className="stack" style={{ gap: 8, marginTop: 12 }}>
            {lines.map((l, idx) => {
              const it = l.item !== "" ? itemById[l.item] : null;
              return (
                <div
                  key={idx}
                  className="stack"
                  style={{
                    gap: 4,
                    ...(l.warn
                      ? {
                          borderLeft: "3px solid var(--danger)",
                          paddingLeft: 8,
                          marginLeft: -11,
                        }
                      : {}),
                  }}
                >
                  {l.hint && (
                    <span className="muted" style={{ fontSize: 12 }}>
                      <Icon name="receipt" size={12} /> в чеке: {l.hint}
                    </span>
                  )}
                  <div className="receipt-line">
                  <select
                    className="input"
                    value={l.item}
                    onChange={(e) => setLine(idx, { item: Number(e.target.value) })}
                  >
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
                    onChange={(e) => setLine(idx, { quantity: e.target.value })}
                    placeholder={it ? `кол-во, ${it.unit_display}` : "кол-во"}
                  />
                  <input
                    className="input"
                    inputMode="decimal"
                    value={l.unit_cost}
                    onChange={(e) => setLine(idx, { unit_cost: e.target.value })}
                    placeholder="цена/ед, ₽"
                  />
                  <button className="icon-btn danger" onClick={() => removeLine(idx)} aria-label="Убрать строку">
                    <Icon name="minus" size={16} />
                  </button>
                  </div>
                </div>
              );
            })}
            <button className="btn sm ghost" onClick={addLine} style={{ alignSelf: "flex-start" }}>
              <Icon name="plus" size={15} /> Строка
            </button>
          </div>

          <button className="btn block" onClick={submitReceipt} disabled={submitting} style={{ marginTop: 14 }}>
            <Icon name={submitting ? "spark" : "check"} size={18} /> Оприходовать
          </button>
        </div>
      )}

      {/* ——— ОСТАТКИ ——— */}
      {tab === "stock" && (
        <>
          <div className="wrap" style={{ gap: 8, margin: "14px 0" }}>
            <button className="btn sm ghost" onClick={() => { setNiOpen((v) => !v); setNiCat(activeCats[0]?.id ?? ""); }}>
              <Icon name="plus" size={15} /> Позиция
            </button>
            <button className="btn sm ghost" onClick={() => setCatOpen((v) => !v)}>
              <Icon name="plus" size={15} /> Категория
            </button>
          </div>

          {catOpen && (
            <div className="card enter" style={{ marginBottom: 12 }}>
              <div className="wrap" style={{ gap: 8 }}>
                <input className="input" style={{ flex: 1, minWidth: 160 }} value={newCatName} onChange={(e) => setNewCatName(e.target.value)} placeholder="Название категории" />
                <button className="btn sm" onClick={createCategory}><Icon name="check" size={16} /> Добавить</button>
              </div>
            </div>
          )}

          {niOpen && (
            <div className="card enter" style={{ marginBottom: 12 }}>
              <strong style={{ fontFamily: "Fredoka", fontSize: 16 }}>Новая позиция</strong>
              <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
                <label className="field">
                  <span className="label">Название</span>
                  <input className="input" value={niName} onChange={(e) => setNiName(e.target.value)} placeholder="напр. Молоко" />
                </label>
                <label className="field">
                  <span className="label">Категория</span>
                  <select className="input" value={niCat} onChange={(e) => setNiCat(Number(e.target.value))}>
                    {activeCats.map((c) => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10, alignItems: "end" }}>
                <div className="field">
                  <span className="label">Единица</span>
                  <div className="wrap" style={{ gap: 6 }}>
                    {UNITS.map((u) => (
                      <button
                        key={u.value}
                        className={"btn sm " + (niUnit === u.value ? "" : "ghost")}
                        onClick={() => setNiUnit(u.value)}
                        style={{ flex: 1 }}
                      >
                        {u.label}
                      </button>
                    ))}
                  </div>
                </div>
                <label className="field">
                  <span className="label">Порог «заканчивается»</span>
                  <input className="input" inputMode="decimal" value={niMin} onChange={(e) => setNiMin(e.target.value)} placeholder="необязательно" />
                </label>
              </div>
              <button className="btn block" onClick={createItem} style={{ marginTop: 12 }}>
                <Icon name="check" size={17} /> Добавить позицию
              </button>
            </div>
          )}

          {activeCats.map((cat) => {
            const catItems = items.filter((i) => i.category === cat.id && i.is_active);
            if (!catItems.length) return null;
            return (
              <section className="menu-section" key={cat.id}>
                <div className="menu-head">
                  <h2><Icon name="box" size={18} /> {cat.name}</h2>
                </div>
                {catItems.map((it) => (
                  <div className="row" key={it.id}>
                    <div className="stack" style={{ gap: 2, flex: 1, minWidth: 0 }}>
                      <strong>{it.name}</strong>
                      <span className="muted" style={{ fontSize: 12 }}>
                        {it.min_quantity ? `порог ${fmtQty(it.min_quantity)} ${it.unit_display}` : "без порога"}
                      </span>
                    </div>

                    {adjustId === it.id ? (
                      <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                        <input
                          className="input"
                          inputMode="decimal"
                          value={adjustQty}
                          onChange={(e) => setAdjustQty(e.target.value)}
                          style={{ width: 92 }}
                          autoFocus
                        />
                        <button className="icon-btn" onClick={() => saveAdjust(it.id)} aria-label="Сохранить"><Icon name="check" size={16} /></button>
                        <button className="icon-btn" onClick={() => setAdjustId(null)} aria-label="Отмена"><Icon name="minus" size={16} /></button>
                      </span>
                    ) : (
                      <span style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>
                        <span className={"chip" + (it.is_low ? " low" : "")} style={{ minWidth: 74, justifyContent: "center" }}>
                          {fmtQty(it.quantity)} {it.unit_display}
                        </span>
                        <button className="icon-btn" onClick={() => startAdjust(it)} aria-label="Корректировка" title="Корректировка / инвентаризация">
                          <Icon name="edit" size={16} />
                        </button>
                      </span>
                    )}
                  </div>
                ))}
              </section>
            );
          })}

          {items.filter((i) => i.is_active).length === 0 && (
            <div className="card" style={{ marginTop: 12, textAlign: "center" }}>
              <p className="muted" style={{ margin: 0 }}>
                Пока нет позиций. Добавьте категорию и позицию, затем оприходуйте товар.
              </p>
            </div>
          )}
        </>
      )}

      {/* ——— ПРИХОДЫ ——— */}
      {tab === "receipts" && (
        <div className="stack" style={{ gap: 12, marginTop: 14 }}>
          {receipts.length === 0 && (
            <div className="card" style={{ textAlign: "center" }}>
              <p className="muted" style={{ margin: 0 }}>Приходов пока нет.</p>
            </div>
          )}
          {receipts.map((r) => (
            <div className="card" key={r.id}>
              <div className="between">
                <strong style={{ fontFamily: "Fredoka", fontSize: 16 }}>
                  Приход №{r.id}{r.supplier ? ` · ${r.supplier}` : ""}
                </strong>
                {Number(r.total_cost) > 0 && (
                  <strong className="num" style={{ color: "var(--brand)" }}>
                    {Number(r.total_cost).toLocaleString("ru")} ₽
                  </strong>
                )}
              </div>
              <span className="muted" style={{ fontSize: 12 }}>
                {fmtDateTime(r.created_at)}{r.received_by_name ? ` · ${r.received_by_name}` : ""}
                {r.comment ? ` · ${r.comment}` : ""}
              </span>
              <ul className="stack" style={{ gap: 4, margin: "10px 0 0", listStyle: "none", padding: 0 }}>
                {r.items.map((li) => (
                  <li key={li.id} className="between">
                    <span>{li.item_name}</span>
                    <span className="num muted">
                      +{fmtQty(li.quantity)} {li.unit_display}
                      {li.subtotal ? ` · ${Number(li.subtotal).toLocaleString("ru")} ₽` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {toast && (
        <div className="toast" role="status">
          <Icon name="spark" size={18} /> {toast}
        </div>
      )}
    </>
  );
}
