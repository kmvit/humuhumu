import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { get, post, ApiError } from "../../api";
import type { Category, Order, Product } from "../../types";
import Icon, { categoryIcon } from "../../components/Icon";
import { SceneBanner, WaveRule } from "../../components/Ornaments";
import Lightbox from "../../components/Lightbox";
import { useToast } from "../../components/ui/Toast";
import Stepper from "../../components/ui/Stepper";
import OrderStatus from "./OrderStatus";
import { useTrackedOrder } from "../../orderTrack";
import { useAppearance, useSite, useFeature } from "../../site";
import { initTable } from "../../table";

export default function Menu() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [activeCat, setActiveCat] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [zoom, setZoom] = useState<string | null>(null);

  const [cart, setCart] = useState<Record<number, number>>({});
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cartOpen, setCartOpen] = useState(false);
  const notify = useToast();
  const { theme } = useAppearance();
  // Стойка: без столов и официанта, заказ забирают по номеру в окне.
  const site = useSite();
  const counter = site?.service_mode === "counter";
  // Телефон в форме заказа нужен только бонусной программе: по нему заказ
  // привяжется к гостю и ему будет что начислить.
  const bonusOn = useFeature("loyalty") && !!site?.bonus_enabled;
  const welcome = site?.bonus_welcome ?? 0;
  const bonusPercent = Number(site?.bonus_earn_percent ?? 0);

  const { token, order: tracked, track, forget, reload: reloadTracked } = useTrackedOrder();
  const [table] = useState<string | null>(initTable);

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
      notify("Укажите имя, чтобы официант нашёл заказ", "bad");
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
        comment: comment.trim(),
        items,
        table: table ?? "",
        ...(bonusOn && phone.trim() ? { phone: phone.trim() } : {}),
      });
      track(order);
      setCart({});
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Ошибка", "bad");
    } finally {
      setSubmitting(false);
    }
  }

  function newOrder() {
    forget();
    setName("");
    setComment("");
  }

  if (tracked) {
    return (
      <>
        <h1 className="h1">Ваш заказ</h1>
        <OrderStatus
          order={tracked}
          token={token}
          onReload={reloadTracked}
          onForget={newOrder}
        />
      </>
    );
  }

  return (
    <>
      <div className="between" style={{ alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <h1 className="h1">Меню</h1>
        {table && !counter && (
          <span className="chip" style={{ fontSize: 15 }}>
            <Icon name="store" size={16} /> Ваш стол №{table}
          </span>
        )}
      </div>
      <p className="muted subtitle">
        {counter
          ? "Соберите заказ и отправьте — заберёте в окне по своему номеру"
          : table
          ? "Соберите заказ — он придёт официанту с вашим столом"
          : "Соберите заказ и отправьте — потом подойдите к стойке"}
      </p>

      {/* Два вида меню. По умолчанию гость попадает на ленту, список — сюда,
          на /menu; выбор дублируем крупными плитками, чтобы вернуться в ленту
          можно было не только кнопкой «назад». */}
      <div className="menu-modes">
        <span className="menu-modes-label">Варианты меню</span>
        <div className="grid cols-2">
          <div className="card mode-tile active">
            <Icon name="receipt" size={26} />
            <strong>Списком</strong>
            <span className="muted">Все блюда с ценами — быстро собрать заказ</span>
          </div>
          <Link className="card hover mode-tile" to="/reels">
            <Icon name="spark" size={26} />
            <strong>Лентой с фото</strong>
            <span className="muted">Листать во весь экран, как в соцсетях</span>
          </Link>
        </div>
      </div>

      {/* сцена с пальмами — только в «Островной» теме */}
      {theme === "island" && (
        <>
          <SceneBanner />
          <WaveRule />
        </>
      )}

      <div className="scroll-x my-4">
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
                  ) : cart[p.id] ? (
                    <Stepper value={cart[p.id]} width={96} onDec={() => remove(p.id)} onInc={() => add(p.id)} />
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
            <strong className="title">Ваш заказ</strong>
            <button className="btn sm ghost" onClick={() => setCartOpen(false)}>Свернуть</button>
          </div>
          <ul className="stack list mt-2">
            {Object.entries(cart).map(([id, qty]) => {
              const p = products.find((x) => x.id === Number(id));
              if (!p) return null;
              return (
                <li key={id} className="between">
                  <span>{p.name} <span className="muted sm">#{p.id}</span></span>
                  <span className="inline">
                    <span className="num muted" style={{ minWidth: 62, textAlign: "right" }}>{(Number(p.price) * qty).toLocaleString("ru")} ₽</span>
                    <Stepper value={qty} width={104} onDec={() => remove(Number(id))} onInc={() => add(Number(id))} />
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="between rule-top mt-3">
            <strong>Итого</strong>
            <strong className="num">{total.toLocaleString("ru")} ₽</strong>
          </div>
          <label className="field mt-4">
            <span className="label">Ваше имя</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Как вас зовут?"
              maxLength={120}
            />
          </label>
          {bonusOn && (
            <label className="field mt-3">
              <span className="label">Телефон — для бонусов</span>
              <input
                className="input"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+7 999 000-00-00"
                inputMode="tel"
                autoComplete="tel"
                maxLength={20}
              />
              <span className="muted sm">
                Необязательно. Начислим {bonusPercent}% с заказа
                {welcome > 0 ? `, а за первый визит ещё ${welcome} бонусов` : ""}.
              </span>
            </label>
          )}
          <label className="field mt-3">
            <span className="label">Комментарий к заказу</span>
            <input
              className="input"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="напр. без лука, аллергия на орехи"
              maxLength={300}
            />
          </label>
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
