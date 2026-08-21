import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { get, post, ApiError } from "../../api";
import type { Category, Order, Product } from "../../types";
import Icon, { categoryIcon, type IconName } from "../../components/Icon";
import { initTable } from "../../table";

const COACH_KEY = "humu_reels_coached";
const DEVICE_KEY = "humu_device";
const LIKES_KEY = "humu_liked";

// стабильный id устройства для анонимных лайков
function initDevice(): string {
  let d = localStorage.getItem(DEVICE_KEY);
  if (!d) {
    d = (crypto.randomUUID?.() ?? String(Date.now()) + Math.random().toString(16).slice(2));
    localStorage.setItem(DEVICE_KEY, d);
  }
  return d;
}

// Экспериментальное меню в стиле Reels: категории листаем влево-вправо,
// позиции внутри категории — вверх-вниз. Только изображения, минимум текста.
export default function MenuReels() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [cart, setCart] = useState<Record<number, number>>({});
  const [cartOpen, setCartOpen] = useState(false);
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [activeIdx, setActiveIdx] = useState(0);
  const [table] = useState<string | null>(initTable);
  const [coach, setCoach] = useState<boolean>(() => !localStorage.getItem(COACH_KEY));
  const [likes, setLikes] = useState<Record<number, number>>({}); // pid -> счётчик
  const [liked, setLiked] = useState<Set<number>>(
    () => new Set(JSON.parse(localStorage.getItem(LIKES_KEY) || "[]"))
  );
  const [device] = useState(initDevice);

  const trackRef = useRef<HTMLDivElement>(null);

  function dismissCoach() {
    if (!coach) return;
    localStorage.setItem(COACH_KEY, "1");
    setCoach(false);
  }

  useEffect(() => {
    Promise.all([
      get<Category[]>("/categories/").then(setCategories),
      get<Product[]>("/products/").then((ps) => {
        setProducts(ps);
        setLikes(Object.fromEntries(ps.map((p) => [p.id, p.likes ?? 0])));
      }),
    ])
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  async function toggleLike(pid: number) {
    const isLiked = liked.has(pid);
    // оптимистично обновляем UI, потом синхронизируем счётчик с сервером
    setLiked((s) => {
      const n = new Set(s);
      isLiked ? n.delete(pid) : n.add(pid);
      localStorage.setItem(LIKES_KEY, JSON.stringify([...n]));
      return n;
    });
    setLikes((m) => ({ ...m, [pid]: Math.max(0, (m[pid] || 0) + (isLiked ? -1 : 1)) }));
    try {
      const res = await post<{ id: number; likes: number }>(
        `/products/${pid}/${isLiked ? "unlike" : "like"}/`,
        { device }
      );
      setLikes((m) => ({ ...m, [pid]: res.likes }));
    } catch {
      /* оставляем оптимистичное значение */
    }
  }

  // страницы пейджера: «Хиты» (топ по лайкам) + категории с позициями.
  // Состав «Хитов» берём из серверных лайков и держим стабильным на сессию,
  // чтобы страница не перетасовывалась под пальцем при каждом лайке.
  const pages = useMemo(() => {
    const catPages = [...categories]
      .sort((a, b) => a.sort_order - b.sort_order)
      .map((c) => ({
        key: `c${c.id}`,
        label: c.name,
        icon: categoryIcon(c.name),
        items: products.filter((p) => p.category === c.id && p.is_available),
      }))
      .filter((s) => s.items.length > 0);
    const top = products
      .filter((p) => p.is_available && (p.likes ?? 0) > 0)
      .sort((a, b) => (b.likes ?? 0) - (a.likes ?? 0))
      .slice(0, 12);
    const topPage = top.length
      ? [{ key: "top", label: "Хиты", icon: "heart" as IconName, items: top }]
      : [];
    return [...topPage, ...catPages];
  }, [categories, products]);

  const priceOf = (pid: number) => Number(products.find((x) => x.id === pid)?.price ?? 0);
  const count = Object.values(cart).reduce((a, b) => a + b, 0);
  const total = Object.entries(cart).reduce((s, [pid, q]) => s + priceOf(Number(pid)) * q, 0);

  const add = (pid: number) => setCart((c) => ({ ...c, [pid]: (c[pid] || 0) + 1 }));
  const remove = (pid: number) =>
    setCart((c) => {
      const n = { ...c, [pid]: (c[pid] || 0) - 1 };
      if (n[pid] <= 0) delete n[pid];
      return n;
    });

  function onTrackScroll() {
    const el = trackRef.current;
    if (!el) return;
    dismissCoach(); // первый свайп — прячем подсказку
    const i = Math.round(el.scrollLeft / el.clientWidth);
    if (i !== activeIdx) setActiveIdx(i);
  }

  function goCat(i: number) {
    const el = trackRef.current;
    if (!el) return;
    // прямое присваивание надёжнее scrollTo на scroll-snap контейнере;
    // плавность даёт scroll-behavior: smooth в CSS
    el.scrollLeft = i * el.clientWidth;
    setActiveIdx(i);
  }

  async function submit() {
    if (!name.trim()) {
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
      await post<Order>("/orders/place/", {
        customer_name: name.trim(),
        items,
        table: table ?? "",
      });
      setCart({});
      setCartOpen(false);
      setName("");
      setToast("Заказ отправлен — подойдёт официант");
      setTimeout(() => setToast(null), 3500);
    } catch (err) {
      setToast(err instanceof ApiError ? err.message : "Ошибка");
      setTimeout(() => setToast(null), 3500);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="reels">
        <div className="reels-loading">Загрузка меню…</div>
      </div>
    );
  }

  if (pages.length === 0) {
    return (
      <div className="reels">
        <div className="reels-loading">
          Нет доступных позиций
          <Link className="btn sm ghost" style={{ marginTop: 14 }} to="/">На обычное меню</Link>
        </div>
      </div>
    );
  }

  return (
    <div className={"reels" + (count > 0 ? " has-cart" : "")}>
      {/* верхняя лента категорий */}
      <div className="reels-top">
        <Link className="reels-back" to="/" aria-label="Обычное меню">
          <span style={{ display: "inline-flex", transform: "rotate(90deg)" }}>
            <Icon name="arrowDown" size={18} />
          </span>
        </Link>
        <div className="reels-cats">
          {pages.map((s, i) => (
            <button
              key={s.key}
              className={"reels-chip" + (i === activeIdx ? " on" : "") + (s.key === "top" ? " top" : "")}
              onClick={() => goCat(i)}
            >
              <Icon name={s.icon} size={15} filled={s.key === "top"} /> {s.label}
            </button>
          ))}
        </div>
      </div>

      {/* горизонтальный пейджер: хиты + категории */}
      <div className="reels-track" ref={trackRef} onScroll={onTrackScroll}>
        {pages.map((s) => (
          <div className="reels-page" key={s.key}>
            {s.items.map((p) => {
              const q = cart[p.id] || 0;
              const likeN = likes[p.id] ?? p.likes ?? 0;
              const isLiked = liked.has(p.id);
              return (
                <article className="reel" key={p.id}>
                  {p.image ? (
                    <img className="reel-img" src={p.image} alt={p.name} loading="lazy" />
                  ) : (
                    <div className="reel-ph">
                      <Icon name={categoryIcon(p.category_name)} size={64} />
                    </div>
                  )}
                  <div className="reel-shade" />
                  <button
                    className={"reel-like" + (isLiked ? " on" : "")}
                    onClick={() => toggleLike(p.id)}
                    aria-label={isLiked ? "Убрать лайк" : "Нравится"}
                  >
                    <Icon name="heart" size={26} filled={isLiked} />
                    {likeN > 0 && <span className="reel-like-n">{likeN}</span>}
                  </button>
                  <div className="reel-body">
                    <div className="reel-info">
                      <h2>{p.name}</h2>
                      {p.description && <p className="reel-desc">{p.description}</p>}
                      <div className="reel-meta">
                        <span className="reel-price">{Number(p.price).toLocaleString("ru")} ₽</span>
                        {p.weight_grams ? <span className="reel-weight">{p.weight_grams} г</span> : null}
                      </div>
                      {p.is_stopped && <span className="stop-badge reel-stop">Sold out</span>}
                    </div>
                    <div className="reel-add-wrap">
                      {p.is_stopped ? null : q > 0 ? (
                        <div className="reel-stepper">
                          <button onClick={() => remove(p.id)} aria-label="Убрать"><Icon name="minus" size={20} /></button>
                          <span className="num">{q}</span>
                          <button onClick={() => add(p.id)} aria-label="Ещё"><Icon name="plus" size={20} /></button>
                        </div>
                      ) : (
                        <button className="reel-add" onClick={() => add(p.id)} aria-label={`Добавить «${p.name}»`}>
                          <Icon name="plus" size={26} />
                        </button>
                      )}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        ))}
      </div>

      {toast && (
        <div className="reels-toast" role="status">
          <Icon name="spark" size={16} /> {toast}
        </div>
      )}

      {/* корзина */}
      {count > 0 && !cartOpen && (
        <button className="reels-cartbar" onClick={() => setCartOpen(true)}>
          <span className="reels-cartbar-l">
            <Icon name="coffee" size={18} /> {count} поз.
          </span>
          <span className="num">{total.toLocaleString("ru")} ₽</span>
        </button>
      )}

      {cartOpen && (
        <div className="reels-sheet-overlay" onClick={(e) => { if (e.target === e.currentTarget) setCartOpen(false); }}>
          <div className="reels-sheet">
            <div className="between">
              <strong style={{ fontFamily: "Fredoka", fontSize: 19 }}>Ваш заказ</strong>
              <button className="icon-btn" onClick={() => setCartOpen(false)} aria-label="Закрыть">
                <span style={{ display: "inline-flex", transform: "rotate(45deg)" }}><Icon name="plus" size={18} /></span>
              </button>
            </div>
            <div className="reels-sheet-items">
              {Object.entries(cart).map(([pid, q]) => {
                const p = products.find((x) => x.id === Number(pid));
                if (!p) return null;
                return (
                  <div className="between reels-sheet-row" key={pid}>
                    <span>{p.name}</span>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
                      <div className="reel-stepper sm">
                        <button onClick={() => remove(p.id)} aria-label="Убрать"><Icon name="minus" size={16} /></button>
                        <span className="num">{q}</span>
                        <button onClick={() => add(p.id)} aria-label="Ещё"><Icon name="plus" size={16} /></button>
                      </div>
                      <span className="num" style={{ minWidth: 64, textAlign: "right" }}>
                        {(priceOf(p.id) * q).toLocaleString("ru")} ₽
                      </span>
                    </span>
                  </div>
                );
              })}
            </div>
            {table && (
              <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
                <Icon name="store" size={14} /> Стол №{table}
              </div>
            )}
            <label className="field" style={{ display: "block", marginTop: 12 }}>
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
            <div className="between" style={{ marginTop: 14 }}>
              <strong className="num" style={{ fontSize: 20 }}>{total.toLocaleString("ru")} ₽</strong>
              <button className="btn" onClick={submit} disabled={submitting}>
                <Icon name={submitting ? "spark" : "check"} size={18} /> Отправить
              </button>
            </div>
          </div>
        </div>
      )}

      {/* одноразовая подсказка по жестам — закрывается тапом, кнопкой или первым свайпом */}
      {coach && !loading && (
        <div className="reels-coach" onClick={dismissCoach}>
          <div className="reels-coach-card" onClick={(e) => e.stopPropagation()}>
            <div className="reels-gesture">
              <div className="reels-gesture-h">
                <span style={{ transform: "rotate(90deg)", display: "inline-flex" }}><Icon name="arrowDown" size={22} /></span>
                <span className="reels-hand" />
                <span style={{ transform: "rotate(-90deg)", display: "inline-flex" }}><Icon name="arrowDown" size={22} /></span>
              </div>
              <strong>Категории</strong>
              <span>свайп влево-вправо</span>
            </div>
            <div className="reels-gesture">
              <div className="reels-gesture-v">
                <Icon name="arrowUp" size={22} />
                <span className="reels-hand v" />
                <Icon name="arrowDown" size={22} />
              </div>
              <strong>Блюда</strong>
              <span>свайп вверх-вниз</span>
            </div>
            <button className="btn block" onClick={dismissCoach}>
              <Icon name="check" size={18} /> Понятно
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
