import { useEffect, useMemo, useState } from "react";
import { get, post, ApiError } from "../../api";
import type { Category, Order, Product } from "../../types";
import Icon, { categoryIcon } from "../../components/Icon";
import Lightbox from "../../components/Lightbox";
import { useToast } from "../../components/ui/Toast";
import Stepper from "../../components/ui/Stepper";

// Сбор заказа для стола. Позиции можно писать на гостя (Общий / Гость 1, 2, …)
// для раздельного счёта — либо оставить всё общим.
export default function Compose({
  table,
  orderId,
  initialGuests = 0,
  onCreated,
  onCancel,
}: {
  table: string;
  orderId?: number; // если задан — дописываем позиции в этот заказ, а не создаём новый
  initialGuests?: number; // сколько именованных гостей уже есть в заказе
  onCreated: () => void;
  onCancel: () => void;
}) {
  const adding = orderId != null;
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [activeCat, setActiveCat] = useState<number | null>(null);
  const [cart, setCart] = useState<Record<string, number>>({}); // "guest:productId" -> qty
  const [guests, setGuests] = useState(initialGuests); // сколько именованных гостей (0 = только общий)
  const [activeGuest, setActiveGuest] = useState(0); // 0 = общий
  const [comment, setComment] = useState("");
  const notify = useToast();
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
      if (adding) {
        await post<Order>(`/orders/${orderId}/add_items/`, { items });
      } else {
        await post<Order>("/orders/", { items, table, comment: comment.trim() });
      }
      onCreated();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Ошибка", "bad");
      setBusy(false);
    }
  }

  return (
    <>
      <div className="between">
        <h1 className="h1">{adding ? `Заказ №${orderId}` : `Стол ${table}`}</h1>
        <button className="btn sm ghost" onClick={onCancel}>Назад</button>
      </div>
      <p className="muted subtitle">
        {adding
          ? `Добавляем позиции в заказ · стол ${table}`
          : "Выберите гостя и добавляйте позиции · можно оставить общим"}
      </p>

      {/* выбор гостя, на которого пишутся позиции */}
      <div className="scroll-x mt-3">
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

      {!adding && (
        <label className="field mt-3">
          <span className="label">Комментарий к заказу</span>
          <input
            className="input"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="напр. без лука, аллергия на орехи, стол у окна"
            maxLength={300}
          />
        </label>
      )}

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
        <div className="stack loose mt-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <div className="skeleton sm" key={i} />
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
              <div className={"menu-row" + (p.is_available && !p.is_stopped ? "" : " out")} key={p.id}>
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
                    <h3>{p.name} <span className="muted sm">#{p.id}</span></h3>
                    {p.description && <p className="menu-desc">{p.description}</p>}
                    {p.is_stopped && <span className="stop-badge">Sold out</span>}
                  </div>
                </div>
                <span className="menu-price num">{Number(p.price).toLocaleString("ru")}</span>
                <div className="menu-add">
                  {p.is_stopped ? (
                    <span className="muted sm">стоп</span>
                  ) : qtyOf(p.id) ? (
                    <Stepper value={qtyOf(p.id)} width={116} onDec={() => remove(p.id)} onInc={() => add(p.id)} />
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
        <div className="card mt-4" style={{ marginBottom: 88 }}>
          <strong className="title">Разбивка</strong>
          <div className="stack loose mt-3">
            {guestList.filter((g) => guestCount(g) > 0).map((g) => (
              <div key={g} className="rule-top">
                <div className="between">
                  <strong>{guestLabel(g)}</strong>
                  <span className="num">{guestTotal(g).toLocaleString("ru")} ₽</span>
                </div>
                <ul className="stack tight list mt-2">
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

      {count > 0 && (
        <div className="cartbar">
          <div className="stack" style={{ gap: 0 }}>
            <span className="muted">{count} поз. · стол {table}{guests > 0 ? ` · пишем на: ${guestLabel(activeGuest)}` : ""}</span>
            <span className="total num">{total.toLocaleString("ru")} ₽</span>
          </div>
          <button className="btn" onClick={submit} disabled={busy}>
            <Icon name={busy ? "spark" : "check"} size={18} />
            {adding ? "Добавить" : "Отправить"}
          </button>
        </div>
      )}

      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </>
  );
}
