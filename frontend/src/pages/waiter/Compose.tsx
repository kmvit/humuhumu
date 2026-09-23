import { useEffect, useMemo, useState } from "react";
import { get, post, ApiError } from "../../api";
import type { Category, Order, Performer, Product, ProductVariant } from "../../types";
import OptionsSheet from "../../components/OptionsSheet";
import { lineCaption, linePrice, lineKey, needsPicking } from "../../cart";
import Icon, { categoryIcon } from "../../components/Icon";
import Lightbox from "../../components/Lightbox";
import { useToast } from "../../components/ui/Toast";
import Stepper from "../../components/ui/Stepper";

// Сбор заказа. В зале — для стола: позиции можно писать на гостя
// (Общий / Гость 1, 2, …) для раздельного счёта либо оставить всё общим.
// На стойке стола нет: заказ принимают на словах, гость получает номер,
// и разбивка по гостям там ни к чему.
export default function Compose({
  table = "",
  orderId,
  initialGuests = 0,
  onCreated,
  onCancel,
}: {
  table?: string; // пусто — формат «стойка»: столов нет, заказ зовут по номеру
  orderId?: number; // если задан — дописываем позиции в этот заказ, а не создаём новый
  initialGuests?: number; // сколько именованных гостей уже есть в заказе
  onCreated: () => void;
  onCancel: () => void;
}) {
  const adding = orderId != null;
  const atTable = table !== "";
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [activeCat, setActiveCat] = useState<number | null>(null);
  // ключ «гость:вариант|опции» — у официанта к строке добавляется ещё и гость
  const [cart, setCart] = useState<Record<string, number>>({});
  // выбранный объём в карточке: id товара → id варианта
  const [picked, setPicked] = useState<Record<number, number>>({});
  // блюдо, для которого открыт лист выбора
  const [picking, setPicking] = useState<Product | null>(null);
  const [guests, setGuests] = useState(initialGuests); // сколько именованных гостей (0 = только общий)
  const [activeGuest, setActiveGuest] = useState(0); // 0 = общий
  const [comment, setComment] = useState("");
  // Кто выполняет заказ. На точке один планшет и общий вход, поэтому по
  // учётной записи не понять, кто из смены это сделал. Поле необязательное.
  const [performers, setPerformers] = useState<Performer[]>([]);
  const [performer, setPerformer] = useState<number | "">("");
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
    // Список смены грузим отдельно: без него заказ принимается как прежде,
    // поэтому ошибка здесь не должна мешать работе.
    get<Performer[]>("/shifts/performers/").then(setPerformers).catch(() => {});
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

  const nameOf = (vid: number) => {
    const found = byVariant.get(vid);
    if (!found) return "";
    return `${found.product.name} ${found.variant.label}`.trim();
  };
  /** Какой объём выбран в карточке. По умолчанию — первый. */
  const variantOf = (p: Product) =>
    p.variants.find((v) => v.id === picked[p.id]) ?? p.variants[0];

  /** «2:15|7,9» — гость, вариант и набор опций. */
  const key = (g: number, vid: number, mods: number[] = []) =>
    `${g}:${lineKey(vid, mods)}`;
  /** Разобрать ключ обратно. */
  const parse = (k: string) => {
    const [guest, rest] = k.split(":");
    const [vid, mods] = rest.split("|");
    return {
      guest: Number(guest),
      variant: Number(vid),
      modifiers: mods ? mods.split(",").map(Number) : [],
    };
  };
  /** Сколько этого объёма у текущего гостя — по всем наборам опций. */
  const qtyOf = (vid: number) =>
    Object.entries(cart).reduce((n, [k, q]) => {
      const l = parse(k);
      return l.guest === activeGuest && l.variant === vid ? n + q : n;
    }, 0);
  const count = Object.values(cart).reduce((a, b) => a + b, 0);
  const total = Object.entries(cart).reduce((s, [k, q]) => {
    const l = parse(k);
    const found = byVariant.get(l.variant);
    return s + (found ? linePrice(found.variant, found.product.modifier_groups, l.modifiers) * q : 0);
  }, 0);

  const guestList = [0, ...Array.from({ length: guests }, (_, i) => i + 1)];
  const guestLabel = (g: number) => (g === 0 ? "Общий" : `Гость ${g}`);
  const guestItems = (g: number) =>
    Object.entries(cart)
      .filter(([k]) => parse(k).guest === g)
      .map(([k, qty]) => {
        const l = parse(k);
        return { key: k, v: byVariant.get(l.variant), mods: l.modifiers, qty };
      })
      .filter(
        (x): x is {
          key: string;
          v: { product: Product; variant: ProductVariant };
          mods: number[];
          qty: number;
        } => !!x.v
      );
  const guestCount = (g: number) => guestItems(g).reduce((s, x) => s + x.qty, 0);
  const guestTotal = (g: number) =>
    guestItems(g).reduce(
      (s, x) => s + linePrice(x.v.variant, x.v.product.modifier_groups, x.mods) * x.qty,
      0
    );

  const add = (vid: number, mods: number[] = []) =>
    setCart((c) => {
      const k = key(activeGuest, vid, mods);
      return { ...c, [k]: (c[k] || 0) + 1 };
    });
  const removeKey = (k: string) =>
    setCart((c) => {
      const n = { ...c, [k]: (c[k] || 0) - 1 };
      if (n[k] <= 0) delete n[k];
      return n;
    });

  /** Блюду с опциями или объёмами нужен лист выбора; обычному — один тап. */
  function tapAdd(p: Product, v: ProductVariant) {
    if (needsPicking(p)) setPicking(p);
    else add(v.id);
  }

  async function submit() {
    setBusy(true);
    try {
      const items = Object.entries(cart).map(([k, quantity]) => {
        const l = parse(k);
        return {
          variant: l.variant,
          quantity,
          guest: l.guest === 0 ? null : l.guest,
          ...(l.modifiers.length ? { modifiers: l.modifiers } : {}),
        };
      });
      if (adding) {
        await post<Order>(`/orders/${orderId}/add_items/`, { items });
      } else {
        await post<Order>("/orders/", {
          items,
          table,
          comment: comment.trim(),
          ...(performer === "" ? {} : { performer }),
        });
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
        <h1 className="h1">
          {adding ? `Заказ №${orderId}` : atTable ? `Стол ${table}` : "Новый заказ"}
        </h1>
        <button className="btn sm ghost" onClick={onCancel}>Назад</button>
      </div>
      <p className="muted subtitle">
        {adding
          ? `Добавляем позиции в заказ${atTable ? ` · стол ${table}` : ""}`
          : atTable
            ? "Выберите гостя и добавляйте позиции · можно оставить общим"
            : "Наберите то, что попросил гость — номер он получит при отправке"}
      </p>

      {/* выбор гостя, на которого пишутся позиции; на стойке не нужен */}
      {atTable && (
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
      )}

      {!adding && performers.length > 1 && (
        <label className="field mt-3">
          <span className="label">
            Кто выполняет <span className="muted">— необязательно</span>
          </span>
          <select
            className="input"
            value={performer}
            onChange={(e) => setPerformer(e.target.value === "" ? "" : Number(e.target.value))}
          >
            <option value="">Не указывать</option>
            {performers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.in_shift ? "" : " — не в смене"}
              </option>
            ))}
          </select>
        </label>
      )}

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

            {items.map((p) => {
              const v = variantOf(p);
              if (!v) return null;  // товар без цен не продаём
              const out = !p.is_available || v.is_stopped;
              const simple = !needsPicking(p);
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
                    {/* Размер — первый выбор, дальше «+» уже про него.
                        Чипы в строку: в час пик тап должен остаться один. */}
                    {p.variants.length > 1 && (
                      <div className="size-row" role="group" aria-label="Объём">
                        {p.variants.map((opt) => (
                          <button
                            key={opt.id}
                            className={"size-chip" + (opt.id === v.id ? " active" : "")}
                            aria-pressed={opt.id === v.id}
                            onClick={() => setPicked((st) => ({ ...st, [p.id]: opt.id }))}
                          >
                            {opt.label}
                            {qtyOf(opt.id) > 0 && (
                              <span className="size-n"> · {qtyOf(opt.id)}</span>
                            )}
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
                  ) : simple && qtyOf(v.id) ? (
                    // Степпер — только у простого блюда: с опциями «минус»
                    // не знал бы, какую из строк заказа убавлять.
                    <Stepper
                      value={qtyOf(v.id)}
                      width={116}
                      onDec={() => removeKey(key(activeGuest, v.id))}
                      onInc={() => add(v.id)}
                    />
                  ) : (
                    <button
                      className="btn sm icon"
                      onClick={() => tapAdd(p, v)}
                      disabled={!p.is_available}
                      aria-label={`Добавить «${nameOf(v.id)}»`}
                    >
                      {qtyOf(v.id) > 0 && <span className="num-badge">{qtyOf(v.id)}</span>}
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
                  {guestItems(g).map((x) => {
                    const caption = lineCaption(
                      x.v.variant, x.v.product.modifier_groups, x.mods
                    );
                    return (
                      <li key={x.key} className="between">
                        <span className="row-body">
                          <span>{x.v.product.name}</span>
                          {caption && <span className="muted sm">{caption}</span>}
                        </span>
                        <span className="num muted">× {x.qty}</span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}

      {count > 0 && (
        <div className="cartbar">
          <div className="stack" style={{ gap: 0 }}>
            <span className="muted">
              {count} поз.{atTable ? ` · стол ${table}` : ""}
              {guests > 0 ? ` · пишем на: ${guestLabel(activeGuest)}` : ""}
            </span>
            <span className="total num">{total.toLocaleString("ru")} ₽</span>
          </div>
          <button className="btn" onClick={submit} disabled={busy}>
            <Icon name={busy ? "spark" : "check"} size={18} />
            {adding ? "Добавить" : "Отправить"}
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
