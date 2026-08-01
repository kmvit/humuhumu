import { useEffect, useMemo, useState } from "react";
import { get, post, ApiError } from "../../api";
import type { Category, Order, Product } from "../../types";
import Icon, { categoryIcon } from "../../components/Icon";

export default function Waiter() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [activeCat, setActiveCat] = useState<number | null>(null);
  const [cart, setCart] = useState<Record<number, number>>({});
  const [table, setTable] = useState("");
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([
      get<Category[]>("/categories/").then(setCategories),
      get<Product[]>("/products/").then(setProducts),
    ])
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const sections = useMemo(() => {
    const all = categories
      .map((c) => ({ cat: c, items: products.filter((p) => p.category === c.id) }))
      .filter((s) => s.items.length > 0);
    return activeCat ? all.filter((s) => s.cat.id === activeCat) : all;
  }, [categories, products, activeCat]);
  const total = useMemo(
    () =>
      Object.entries(cart).reduce((s, [id, q]) => {
        const p = products.find((x) => x.id === Number(id));
        return s + (p ? Number(p.price) * q : 0);
      }, 0),
    [cart, products]
  );
  const count = Object.values(cart).reduce((a, b) => a + b, 0);

  const add = (id: number) => setCart((c) => ({ ...c, [id]: (c[id] || 0) + 1 }));
  const remove = (id: number) =>
    setCart((c) => {
      const n = { ...c, [id]: (c[id] || 0) - 1 };
      if (n[id] <= 0) delete n[id];
      return n;
    });

  async function submit() {
    setBusy(true);
    try {
      const items = Object.entries(cart).map(([product, quantity]) => ({
        product: Number(product),
        quantity,
      }));
      const order = await post<Order>("/orders/", { items, table: table.trim() });
      setCart({});
      setTable("");
      setToast({ ok: true, text: `Заказ №${order.id} отправлен на кухню` });
    } catch (err) {
      setToast({ ok: false, text: err instanceof ApiError ? err.message : "Ошибка" });
    } finally {
      setBusy(false);
      setTimeout(() => setToast(null), 3500);
    }
  }

  return (
    <>
      <h1 className="h1">Новый заказ</h1>
      <p className="muted" style={{ marginTop: 4 }}>
        Соберите заказ и отправьте на кухню
      </p>

      <div className="field" style={{ marginTop: 16, maxWidth: 220 }}>
        <label className="label">Номер стола</label>
        <input
          className="input"
          value={table}
          onChange={(e) => setTable(e.target.value)}
          placeholder="напр. 5"
          inputMode="numeric"
        />
      </div>

      <div className="scroll-x" style={{ margin: "16px 0" }}>
        <button className={"navlink" + (activeCat === null ? " active" : "")} onClick={() => setActiveCat(null)}>
          <Icon name="spark" size={16} /> Все
        </button>
        {categories.map((c) => (
          <button
            key={c.id}
            className={"navlink" + (activeCat === c.id ? " active" : "")}
            onClick={() => setActiveCat(c.id)}
          >
            <Icon name={categoryIcon(c.name)} size={16} /> {c.name}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="stack" style={{ gap: 14, marginTop: 24 }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div className="skeleton" style={{ height: 56 }} key={i} />
          ))}
        </div>
      ) : (
        sections.map(({ cat, items }) => (
          <section className="menu-section enter" key={cat.id}>
            <div className="menu-head">
              <h2>
                <Icon name={categoryIcon(cat.name)} size={18} /> {cat.name}
              </h2>
              <span className="unit">руб</span>
            </div>

            {items.map((p) => (
              <div className={"menu-row" + (p.is_available ? "" : " out")} key={p.id}>
                <div className="menu-item">
                  <h3>{p.name}</h3>
                  {p.description && <p className="menu-desc">{p.description}</p>}
                </div>
                <span className="menu-price num">{Number(p.price).toLocaleString("ru")}</span>
                <div className="menu-add">
                  {cart[p.id] ? (
                    <div className="stepper" style={{ width: 116 }}>
                      <button onClick={() => remove(p.id)} aria-label="Убрать"><Icon name="minus" size={16} /></button>
                      <span className="count num">{cart[p.id]}</span>
                      <button onClick={() => add(p.id)} aria-label="Добавить"><Icon name="plus" size={16} /></button>
                    </div>
                  ) : (
                    <button
                      className="btn sm icon"
                      onClick={() => add(p.id)}
                      disabled={!p.is_available}
                      aria-label={`Добавить «${p.name}»`}
                    >
                      <Icon name={p.is_available ? "plus" : "spark"} size={16} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </section>
        ))
      )}

      {toast && (
        <div className={"toast " + (toast.ok ? "ok" : "bad")} role="status">
          <Icon name={toast.ok ? "check" : "spark"} size={18} /> {toast.text}
        </div>
      )}

      {count > 0 && (
        <div className="cartbar">
          <div className="stack" style={{ gap: 0 }}>
            <span className="muted">{count} поз.{table ? ` · стол ${table}` : ""}</span>
            <span className="total num">{total.toLocaleString("ru")} ₽</span>
          </div>
          <button className="btn" onClick={submit} disabled={busy}>
            <Icon name={busy ? "spark" : "check"} size={18} />
            На кухню
          </button>
        </div>
      )}
    </>
  );
}
