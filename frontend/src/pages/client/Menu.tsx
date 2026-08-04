import { useEffect, useMemo, useState } from "react";
import { get, post, ApiError } from "../../api";
import type { Category, Order, Product } from "../../types";
import Icon, { categoryIcon } from "../../components/Icon";
import { SceneBanner, WaveRule } from "../../components/Ornaments";
import Lightbox from "../../components/Lightbox";

const TOKEN_KEY = "humu_order_token";
const TABLE_KEY = "humu_table";

// Стол берём из QR-кода на столе (?table=N) и запоминаем в localStorage,
// чтобы он подставлялся в заказ без участия официанта.
function initTable(): string | null {
  const fromUrl = new URLSearchParams(window.location.search).get("table");
  if (fromUrl) {
    localStorage.setItem(TABLE_KEY, fromUrl);
    return fromUrl;
  }
  return localStorage.getItem(TABLE_KEY);
}

export default function Menu() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [activeCat, setActiveCat] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [zoom, setZoom] = useState<string | null>(null);

  const [cart, setCart] = useState<Record<number, number>>({});
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cartOpen, setCartOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [tracked, setTracked] = useState<Order | null>(null);
  const [table] = useState<string | null>(initTable);
  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  // убираем ?table из адреса — значение уже сохранено, чтобы не мозолило глаз
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("table")) {
      const u = new URL(window.location.href);
      u.searchParams.delete("table");
      window.history.replaceState({}, "", u.pathname + u.search + u.hash);
    }
  }, []);

  useEffect(() => {
    Promise.all([
      get<Category[]>("/categories/").then(setCategories),
      get<Product[]>("/products/").then(setProducts),
    ])
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // отслеживание своего заказа по токену (пока он есть в localStorage)
  useEffect(() => {
    if (!token) {
      setTracked(null);
      return;
    }
    let stop = false;
    const poll = () =>
      get<Order>(`/orders/track/?token=${token}`)
        .then((o) => !stop && setTracked(o))
        .catch((e) => {
          if (e instanceof ApiError && e.status === 404) {
            localStorage.removeItem(TOKEN_KEY);
            if (!stop) { setToken(null); setTracked(null); }
          }
        });
    poll();
    const t = window.setInterval(poll, 5000);
    return () => { stop = true; window.clearInterval(t); };
  }, [token]);

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

  // при появлении/смене своего заказа показываем его карточку сверху страницы
  // (после отправки пользователь остаётся внизу, где была корзина)
  useEffect(() => {
    if (tracked) window.scrollTo(0, 0);
  }, [tracked?.id]);

  const add = (id: number) => setCart((c) => ({ ...c, [id]: (c[id] || 0) + 1 }));
  const remove = (id: number) =>
    setCart((c) => {
      const n = { ...c, [id]: (c[id] || 0) - 1 };
      if (n[id] <= 0) delete n[id];
      return n;
    });

  async function submit() {
    if (!name.trim()) {
      setCartOpen(true);
      setToast("Укажите имя, чтобы официант нашёл заказ");
      setTimeout(() => setToast(null), 3000);
      return;
    }
    setSubmitting(true);
    try {
      const items = Object.entries(cart).map(([product, quantity]) => ({
        product: Number(product),
        quantity,
      }));
      const order = await post<Order>("/orders/place/", {
        customer_name: name.trim(),
        items,
        table: table ?? "",
      });
      if (order.public_token) {
        localStorage.setItem(TOKEN_KEY, order.public_token);
        setToken(order.public_token);
        setTracked(order);
      }
      setCart({});
    } catch (err) {
      setToast(err instanceof ApiError ? err.message : "Ошибка");
      setTimeout(() => setToast(null), 3500);
    } finally {
      setSubmitting(false);
    }
  }

  function newOrder() {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setTracked(null);
    setName("");
  }

  // клиент отменяет свою заявку, пока официант её не подтвердил
  async function cancelRequest() {
    if (!token) return;
    setCancelling(true);
    try {
      await post("/orders/cancel_request/", { token });
      newOrder();
    } catch (err) {
      setToast(err instanceof ApiError ? err.message : "Не удалось отменить");
      setTimeout(() => setToast(null), 3500);
    } finally {
      setCancelling(false);
      setConfirmCancel(false);
    }
  }

  // --- экран статуса своего заказа ---
  if (tracked) {
    const st = tracked.status;
    const head =
      st === "requested" ? "Заявка принята"
      : st === "open" ? (tracked.is_ready ? "Готово!" : "Готовится")
      : st === "paid" ? "Заказ закрыт"
      : "Заказ отменён";
    const note =
      st === "requested" ? `Подойдите к стойке и назовите имя «${tracked.customer_name}» — официант оформит заказ.`
      : st === "open" ? (tracked.is_ready ? "Ваш заказ готов, можно забирать." : `Заказ готовится${tracked.table ? `, стол ${tracked.table}` : ""}.`)
      : st === "paid" ? "Спасибо, что были у нас!"
      : "Заказ отменён.";
    return (
      <>
        <h1 className="h1">Ваш заказ</h1>
        <div className="card enter" style={{ marginTop: 16 }}>
          <div className="between">
            <strong style={{ fontFamily: "Fredoka", fontSize: 20 }}>{head}</strong>
            {st !== "requested" && (
              <span className={"badge " + (tracked.is_ready ? "ready" : st === "open" ? "preparing" : st === "paid" ? "paid" : "cancelled")}>
                {tracked.status_display}
              </span>
            )}
          </div>
          <p className="muted" style={{ marginTop: 6 }}>{note}</p>
          <ul className="stack" style={{ gap: 4, margin: "14px 0 0", listStyle: "none", padding: 0 }}>
            {tracked.items.map((it) => (
              <li key={it.id} className="between">
                <span>{it.product_name}</span>
                <span className="num muted">× {it.quantity}</span>
              </li>
            ))}
          </ul>
          <div className="between" style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            <strong>Итого</strong>
            <strong className="num">{Number(tracked.total).toLocaleString("ru")} ₽</strong>
          </div>
        </div>
        {st === "requested" ? (
          confirmCancel ? (
            <div className="wrap" style={{ marginTop: 16, gap: 8, justifyContent: "center" }}>
              <span className="muted" style={{ alignSelf: "center" }}>Точно отменить заказ?</span>
              <button className="btn sm" style={{ background: "var(--danger)", color: "#fff" }} disabled={cancelling} onClick={cancelRequest}>
                <Icon name="check" size={16} /> Да, отменить
              </button>
              <button className="btn sm ghost" onClick={() => setConfirmCancel(false)}>Нет</button>
            </div>
          ) : (
            <button className="btn ghost block" style={{ marginTop: 16 }} onClick={() => setConfirmCancel(true)}>
              <Icon name="minus" size={18} /> Отменить заказ
            </button>
          )
        ) : (
          <button className="btn ghost block" style={{ marginTop: 16 }} onClick={newOrder}>
            <Icon name="plus" size={18} /> Новый заказ
          </button>
        )}

        {toast && (
          <div className="toast bad" role="status">
            <Icon name="spark" size={18} /> {toast}
          </div>
        )}
      </>
    );
  }

  return (
    <>
      <div className="between" style={{ alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <h1 className="h1">Меню</h1>
        {table && (
          <span className="chip" style={{ fontSize: 15 }}>
            <Icon name="store" size={16} /> Ваш стол №{table}
          </span>
        )}
      </div>
      <p className="muted" style={{ marginTop: 4 }}>
        {table
          ? "Соберите заказ — он придёт официанту с вашим столом"
          : "Соберите заказ и отправьте — потом подойдите к стойке"}
      </p>

      <SceneBanner />
      <WaveRule />

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
                  {cart[p.id] ? (
                    <div className="stepper" style={{ width: 96 }}>
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

      {count > 0 && cartOpen && (
        <div className="cart-sheet">
          <div className="between" style={{ marginBottom: 4 }}>
            <strong style={{ fontFamily: "Fredoka", fontSize: 18 }}>Ваш заказ</strong>
            <button className="btn sm ghost" onClick={() => setCartOpen(false)}>Свернуть</button>
          </div>
          <ul className="stack" style={{ gap: 10, margin: "8px 0 0", listStyle: "none", padding: 0 }}>
            {Object.entries(cart).map(([id, qty]) => {
              const p = products.find((x) => x.id === Number(id));
              if (!p) return null;
              return (
                <li key={id} className="between" style={{ gap: 10 }}>
                  <span>{p.name} <span className="muted" style={{ fontSize: 12 }}>#{p.id}</span></span>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
                    <span className="num muted" style={{ minWidth: 62, textAlign: "right" }}>{(Number(p.price) * qty).toLocaleString("ru")} ₽</span>
                    <div className="stepper" style={{ width: 104 }}>
                      <button onClick={() => remove(Number(id))} aria-label="Убрать"><Icon name="minus" size={15} /></button>
                      <span className="count num">{qty}</span>
                      <button onClick={() => add(Number(id))} aria-label="Добавить"><Icon name="plus" size={15} /></button>
                    </div>
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="between" style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            <strong>Итого</strong>
            <strong className="num">{total.toLocaleString("ru")} ₽</strong>
          </div>
          <label className="field" style={{ display: "block", margin: "14px 0 0" }}>
            <span className="muted" style={{ fontSize: 13 }}>Ваше имя</span>
            <input
              className="input"
              style={{ marginTop: 6 }}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Как вас зовут?"
              maxLength={120}
            />
          </label>
        </div>
      )}

      {toast && (
        <div className="toast bad" role="status">
          <Icon name="spark" size={18} /> {toast}
        </div>
      )}

      {count > 0 && (
        <div className="cartbar">
          <button className="cart-toggle" onClick={() => setCartOpen((o) => !o)}>
            <Icon name={cartOpen ? "minus" : "plus"} size={16} />
            <span className="stack" style={{ gap: 0, alignItems: "flex-start" }}>
              <span className="muted">Ваш заказ · {count} поз.{cartOpen ? "" : " · посмотреть"}</span>
              <span className="total num">{total.toLocaleString("ru")} ₽</span>
            </span>
          </button>
          <button className="btn" onClick={submit} disabled={submitting}>
            <Icon name={submitting ? "spark" : "check"} size={18} />
            Отправить
          </button>
        </div>
      )}

      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </>
  );
}
