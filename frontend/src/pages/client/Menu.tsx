import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { get, post, ApiError } from "../../api";
import type { Category, Order, Product, ProductVariant } from "../../types";
import OptionsSheet from "../../components/OptionsSheet";
import {
  addLine, cartCount, lineCaption, linePrice, lineKey,
  needsPicking, removeLine, toPayload, variantCount, type Cart,
} from "../../cart";
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

  // Ключ строки — вариант ВМЕСТЕ с набором опций: латте на коровьем и на
  // овсяном это разные позиции с разной ценой (см. cart.ts).
  const [cart, setCart] = useState<Cart>({});
  // выбранный объём в карточке: id товара → id варианта
  const [picked, setPicked] = useState<Record<number, number>>({});
  // блюдо, для которого открыт лист выбора объёма и опций
  const [picking, setPicking] = useState<Product | null>(null);
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
  /** Корзина держится на вариантах: продаётся объём, а не карточка. */
  const byVariant = useMemo(() => {
    const map = new Map<number, { product: Product; variant: ProductVariant }>();
    for (const product of products)
      for (const variant of product.variants) map.set(variant.id, { product, variant });
    return map;
  }, [products]);

  const total = useMemo(
    () =>
      Object.values(cart).reduce((s, line) => {
        const found = byVariant.get(line.variant);
        if (!found) return s;
        return (
          s +
          linePrice(found.variant, found.product.modifier_groups, line.modifiers) *
            line.qty
        );
      }, 0),
    [cart, byVariant]
  );

  /** Какой объём выбран в карточке. По умолчанию — первый (самый ходовой). */
  const pickedFor = (p: Product) => picked[p.id] ?? p.variants[0]?.id;
  const variantOf = (p: Product) =>
    p.variants.find((v) => v.id === pickedFor(p)) ?? p.variants[0];
  const count = cartCount(cart);

  // при появлении/смене своего заказа показываем его карточку сверху страницы
  // (после отправки пользователь остаётся внизу, где была корзина)
  useEffect(() => {
    if (tracked) window.scrollTo(0, 0);
  }, [tracked?.id]);

  const add = (variant: number, modifiers: number[] = []) =>
    setCart((c) => addLine(c, variant, modifiers));
  const remove = (key: string) => setCart((c) => removeLine(c, key));

  /** «+» на карточке: у блюда с опциями или объёмами — лист выбора,
   *  у обычного — сразу в заказ, чтобы не плодить лишний тап. */
  function tapAdd(p: Product, variant: ProductVariant) {
    if (needsPicking(p)) setPicking(p);
    else add(variant.id);
  }

  async function submit() {
    if (!name.trim()) {
      setCartOpen(true);
      notify("Укажите имя, чтобы официант нашёл заказ", "bad");
      return;
    }
    setSubmitting(true);
    try {
      const items = toPayload(cart);
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

            {items.map((p) => {
              const v = variantOf(p);
              if (!v) return null;  // товар без цен в меню не показываем
              const out = !p.is_available || v.is_stopped;
              const simple = !needsPicking(p);
              const inCart = variantCount(cart, v.id);
              return (
              <div className={"menu-row" + (out ? " out" : "")} key={p.id}>
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
                    {/* Фото нарисовано нейросетью — гость вправе знать, что
                        перед ним не снимок его порции. */}
                    {p.image_is_generated && p.thumbnail && (
                      <p className="menu-desc">Фото — иллюстрация</p>
                    )}
                    {p.variants.length > 1 && (
                      <div className="size-row" role="group" aria-label="Объём">
                        {p.variants.map((opt) => (
                          <button
                            key={opt.id}
                            className={"size-chip" + (opt.id === v.id ? " active" : "")}
                            aria-pressed={opt.id === v.id}
                            onClick={() => setPicked((s) => ({ ...s, [p.id]: opt.id }))}
                          >
                            {opt.label}
                            {opt.is_stopped && <span className="size-out"> · стоп</span>}
                          </button>
                        ))}
                      </div>
                    )}
                    {v.is_stopped && <span className="stop-badge">Sold out</span>}
                  </div>
                </div>
                <span className="menu-price num">{Number(v.price).toLocaleString("ru")}</span>
                <div className="menu-add">
                  {v.is_stopped ? (
                    <span className="muted sm">стоп</span>
                  ) : simple && cart[lineKey(v.id, [])] ? (
                    // Степпер только у простого блюда: у блюда с опциями
                    // «минус» не знал бы, какую из строк заказа убавлять.
                    <Stepper
                      value={cart[lineKey(v.id, [])].qty}
                      width={96}
                      onDec={() => remove(lineKey(v.id, []))}
                      onInc={() => add(v.id)}
                    />
                  ) : (
                    <button
                      className="btn sm icon"
                      onClick={() => tapAdd(p, v)}
                      disabled={!p.is_available}
                      aria-label={`Добавить «${p.name} ${v.label}`.trim() + "»"}
                    >
                      {inCart > 0 && <span className="num-badge">{inCart}</span>}
                      <Icon name={p.is_available ? "plus" : "spark"} size={16} />
                    </button>
                  )}
                </div>
              </div>
              );
            })}
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
            {Object.entries(cart).map(([key, line]) => {
              const found = byVariant.get(line.variant);
              if (!found) return null;
              const { product: p, variant: v } = found;
              const caption = lineCaption(v, p.modifier_groups, line.modifiers);
              const each = linePrice(v, p.modifier_groups, line.modifiers);
              return (
                <li key={key} className="between">
                  <span className="row-body">
                    <span>
                      {p.name} <span className="muted sm">#{p.id}</span>
                    </span>
                    {caption && <span className="muted sm">{caption}</span>}
                  </span>
                  <span className="inline">
                    <span className="num muted" style={{ minWidth: 62, textAlign: "right" }}>{(each * line.qty).toLocaleString("ru")} ₽</span>
                    <Stepper
                      value={line.qty}
                      width={104}
                      onDec={() => remove(key)}
                      onInc={() => add(line.variant, line.modifiers)}
                    />
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

      {picking && (
        <OptionsSheet
          product={picking}
          onClose={() => setPicking(null)}
          onAdd={(variant, modifiers) => {
            add(variant, modifiers);
            setPicking(null);
          }}
        />
      )}

      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </>
  );
}
