import { useEffect, useMemo, useState } from "react";
import { get, post, ApiError } from "../../api";
import type { Category, Order, Product } from "../../types";
import Icon, { categoryIcon } from "../../components/Icon";
import Lightbox from "../../components/Lightbox";

// Сбор заказа для стола. Позиции можно писать на гостя (Общий / Гость 1, 2, …)
// для раздельного счёта — либо оставить всё общим.
export default function Compose({
  table,
  onCreated,
  onCancel,
}: {
  table: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [activeCat, setActiveCat] = useState<number | null>(null);
  const [cart, setCart] = useState<Record<string, number>>({}); // "guest:productId" -> qty
  const [guests, setGuests] = useState(0); // сколько именованных гостей (0 = только общий)
  const [activeGuest, setActiveGuest] = useState(0); // 0 = общий
  const [toast, setToast] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [zoom, setZoom] = useState<string | null>(null);

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

  const priceOf = (pid: number) => Number(products.find((x) => x.id === pid)?.price ?? 0);
  const key = (g: number, pid: number) => `${g}:${pid}`;
  const qtyOf = (pid: number) => cart[key(activeGuest, pid)] || 0;
  const count = Object.values(cart).reduce((a, b) => a + b, 0);
  const total = Object.entries(cart).reduce((s, [k, q]) => s + priceOf(Number(k.split(":")[1])) * q, 0);

  const guestList = [0, ...Array.from({ length: guests }, (_, i) => i + 1)];
  const guestLabel = (g: number) => (g === 0 ? "Общий" : `Гость ${g}`);
  const guestItems = (g: number) =>
    Object.entries(cart)
      .filter(([k]) => Number(k.split(":")[0]) === g)
      .map(([k, qty]) => ({ p: products.find((x) => x.id === Number(k.split(":")[1])), qty }))
      .filter((x): x is { p: Product; qty: number } => !!x.p);
  const guestCount = (g: number) => guestItems(g).reduce((s, x) => s + x.qty, 0);
  const guestTotal = (g: number) => guestItems(g).reduce((s, x) => s + Number(x.p.price) * x.qty, 0);

  const add = (pid: number) =>
    setCart((c) => ({ ...c, [key(activeGuest, pid)]: (c[key(activeGuest, pid)] || 0) + 1 }));
  const remove = (pid: number) =>
    setCart((c) => {
      const k = key(activeGuest, pid);
      const n = { ...c, [k]: (c[k] || 0) - 1 };
      if (n[k] <= 0) delete n[k];
      return n;
    });

  async function submit() {
    setBusy(true);
    try {
      const items = Object.entries(cart).map(([k, quantity]) => {
        const [g, pid] = k.split(":").map(Number);
        return { product: pid, quantity, guest: g === 0 ? null : g };
      });
      await post<Order>("/orders/", { items, table });
      onCreated();
    } catch (err) {
      setToast(err instanceof ApiError ? err.message : "Ошибка");
      setBusy(false);
      setTimeout(() => setToast(null), 3500);
    }
  }

  return (
    <>
      <div className="between">
        <h1 className="h1">Стол {table}</h1>
        <button className="btn sm ghost" onClick={onCancel}>Назад</button>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>
        Выберите гостя и добавляйте позиции · можно оставить общим
      </p>

      {/* выбор гостя, на которого пишутся позиции */}
      <div className="scroll-x" style={{ marginTop: 12 }}>
        {guestList.map((g) => (
          <button
            key={g}
            className={"navlink" + (activeGuest === g ? " active" : "")}
            onClick={() => setActiveGuest(g)}
          >
            <Icon name={g === 0 ? "spark" : "user"} size={15} /> {guestLabel(g)}
            {guestCount(g) > 0 ? ` · ${guestCount(g)}` : ""}
          </button>
        ))}
        <button
          className="navlink"
          onClick={() => {
            const ng = guests + 1;
            setGuests(ng);
            setActiveGuest(ng);
          }}
        >
          <Icon name="plus" size={15} /> гость
        </button>
      </div>

      <div className="scroll-x" style={{ margin: "10px 0 4px" }}>
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
                <div className="menu-lead">
                  {p.thumbnail && (
                    <img
                      className="menu-thumb zoomable"
                      src={p.thumbnail}
                      alt=""
                      loading="lazy"
                      onClick={() => p.image && setZoom(p.image)}
                    />
                  )}
                  <div className="menu-item">
                    <h3>{p.name} <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>#{p.id}</span></h3>
                    {p.description && <p className="menu-desc">{p.description}</p>}
                  </div>
                </div>
                <span className="menu-price num">{Number(p.price).toLocaleString("ru")}</span>
                <div className="menu-add">
                  {qtyOf(p.id) ? (
                    <div className="stepper" style={{ width: 116 }}>
                      <button onClick={() => remove(p.id)} aria-label="Убрать"><Icon name="minus" size={16} /></button>
                      <span className="count num">{qtyOf(p.id)}</span>
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

      {/* разбивка по гостям перед отправкой */}
      {count > 0 && guests > 0 && (
        <div className="card" style={{ marginTop: 18, marginBottom: 88 }}>
          <strong style={{ fontFamily: "Fredoka", fontSize: 17 }}>Разбивка</strong>
          <div className="stack" style={{ gap: 12, marginTop: 10 }}>
            {guestList.filter((g) => guestCount(g) > 0).map((g) => (
              <div key={g} style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                <div className="between">
                  <strong>{guestLabel(g)}</strong>
                  <span className="num">{guestTotal(g).toLocaleString("ru")} ₽</span>
                </div>
                <ul className="stack" style={{ gap: 2, margin: "6px 0 0", listStyle: "none", padding: 0 }}>
                  {guestItems(g).map((x) => (
                    <li key={x.p.id} className="between">
                      <span>{x.p.name}</span>
                      <span className="num muted">× {x.qty}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}

      {toast && (
        <div className="toast bad" role="status">
          <Icon name="spark" size={18} /> {toast}
        </div>
      )}

      {count > 0 && (
        <div className="cartbar">
          <div className="stack" style={{ gap: 0 }}>
            <span className="muted">{count} поз. · стол {table}{guests > 0 ? ` · пишем на: ${guestLabel(activeGuest)}` : ""}</span>
            <span className="total num">{total.toLocaleString("ru")} ₽</span>
          </div>
          <button className="btn" onClick={submit} disabled={busy}>
            <Icon name={busy ? "spark" : "check"} size={18} />
            Отправить
          </button>
        </div>
      )}

      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </>
  );
}
