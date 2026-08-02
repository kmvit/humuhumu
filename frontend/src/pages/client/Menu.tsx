import { useEffect, useMemo, useState } from "react";
import { get } from "../../api";
import type { Category, Product } from "../../types";
import Icon, { categoryIcon } from "../../components/Icon";
import { SceneBanner, WaveRule } from "../../components/Ornaments";

export default function Menu() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [activeCat, setActiveCat] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      get<Category[]>("/categories/").then(setCategories),
      get<Product[]>("/products/").then(setProducts),
    ])
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // меню собираем разделами, как в печатном: заголовок категории + строки блюд
  const sections = useMemo(() => {
    const all = categories
      .map((c) => ({ cat: c, items: products.filter((p) => p.category === c.id) }))
      .filter((s) => s.items.length > 0);
    return activeCat ? all.filter((s) => s.cat.id === activeCat) : all;
  }, [categories, products, activeCat]);

  return (
    <>
      <p className="script" style={{ fontSize: 22, color: "var(--brand-2)", marginBottom: -4 }}>
        алоха!
      </p>
      <h1 className="h1">Меню</h1>
      <p className="muted" style={{ marginTop: 4 }}>
        Позовите официанта, чтобы сделать заказ
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
                  {p.image && (
                    <img className="menu-thumb" src={p.image} alt="" loading="lazy" />
                  )}
                  <div className="menu-item">
                    <h3>{p.name}</h3>
                    {p.description && <p className="menu-desc">{p.description}</p>}
                  </div>
                </div>
                <span className="menu-price num">{Number(p.price).toLocaleString("ru")}</span>
                {!p.is_available && <span className="muted" style={{ fontSize: 13 }}>нет</span>}
              </div>
            ))}
          </section>
        ))
      )}
    </>
  );
}
