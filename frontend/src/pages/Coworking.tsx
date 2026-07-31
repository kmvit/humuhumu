import { useSite } from "../site";
import Icon, { type IconName } from "../components/Icon";
import { SceneBanner, WaveRule } from "../components/Ornaments";

/* ---------------------------------------------------------------
   Всё содержимое страницы — в этих трёх списках. Правьте прямо тут:
   тексты и цены подставятся в вёрстку автоматически.
   --------------------------------------------------------------- */

const DAY_PRICE = 600;

const INCLUDED: { icon: IconName; title: string; text: string }[] = [
  { icon: "wave", title: "Wi-Fi", text: "Быстрый интернет на весь день" },
  { icon: "spark", title: "Розетка", text: "У каждого рабочего места" },
  { icon: "coffee", title: "Кофе рядом", text: "Вся барная карта — за соседней стойкой" },
  { icon: "leaf", title: "Тихая зона", text: "Место, где можно спокойно поработать" },
  { icon: "palm", title: "Островной вайб", text: "Пальмы, музыка и свет как на побережье" },
  { icon: "user", title: "Своё место", text: "Занимаете стол на весь день посещения" },
];

const DEPOSITS: { amount: number; discount: number }[] = [
  { amount: 10000, discount: 5 },
  { amount: 20000, discount: 10 },
  { amount: 30000, discount: 15 },
];

const money = (n: number) => n.toLocaleString("ru");

export default function Coworking() {
  const site = useSite();

  return (
    <>
      <p className="script">коворкинг</p>
      <h1 className="h1">Работать у океана</h1>
      <p className="muted" style={{ marginTop: 6, maxWidth: "58ch" }}>
        Место для работы внутри кафе: приходите с ноутбуком, занимаете стол и
        остаётесь на весь день. Кофе, вода и Wi-Fi — рядом.
      </p>

      <SceneBanner />
      <WaveRule />

      {/* ——— Разовое посещение ——— */}
      <section className="menu-section">
        <div className="menu-head">
          <h2>
            <Icon name="spark" size={18} /> Тарифы
          </h2>
          <span className="unit">руб</span>
        </div>

        <div className="tariff-hero card">
          <div>
            <h3>День посещения</h3>
            <p className="muted" style={{ marginTop: 4 }}>
              Один полный день в коворкинге{site?.working_hours ? ` · ${site.working_hours}` : ""}
            </p>
          </div>
          <div className="tariff-price">
            <span className="num">{money(DAY_PRICE)}</span>
            <span className="unit">₽ / день</span>
          </div>
        </div>
      </section>

      {/* ——— Что входит ——— */}
      <section className="menu-section">
        <div className="menu-head">
          <h2>
            <Icon name="check" size={18} /> Что входит
          </h2>
        </div>
        <div className="grid stagger" style={{ marginTop: 16 }}>
          {INCLUDED.map((f) => (
            <div className="card feature" key={f.title}>
              <span className="feature-icon">
                <Icon name={f.icon} size={20} />
              </span>
              <h3>{f.title}</h3>
              <p className="muted">{f.text}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ——— Депозит и скидки ——— */}
      <section className="menu-section">
        <div className="menu-head">
          <h2>
            <Icon name="gift" size={18} /> Программа скидок
          </h2>
          <span className="unit">депозит</span>
        </div>
        <p className="muted" style={{ margin: "12px 0 4px", maxWidth: "58ch" }}>
          Пополняете счёт на стойке — и все дальнейшие посещения идут со скидкой.
          Чем больше сумма, тем дешевле день.
        </p>

        <div className="grid stagger tariffs">
          {DEPOSITS.map((d) => {
            const dayPrice = Math.round(DAY_PRICE * (1 - d.discount / 100));
            const days = Math.floor(d.amount / dayPrice);
            return (
              <div className="card hover tariff" key={d.amount}>
                <span className="tariff-badge">−{d.discount}%</span>
                <p className="tariff-amount num">{money(d.amount)} ₽</p>
                <p className="muted">пополнение счёта</p>

                <div className="tariff-rows">
                  <div className="between">
                    <span className="muted">День выходит</span>
                    <span className="num" style={{ fontWeight: 600 }}>
                      {money(dayPrice)} ₽
                    </span>
                  </div>
                  <div className="between">
                    <span className="muted">Хватит примерно на</span>
                    <span className="num" style={{ fontWeight: 600 }}>
                      {days} дн.
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <p className="muted" style={{ marginTop: 14 }}>
          Скидка действует на посещения коворкинга и не сгорает — остаток
          депозита всегда на вашем счёте.
        </p>
      </section>

      {/* ——— Как начать ——— */}
      <section className="menu-section">
        <div className="menu-head">
          <h2>
            <Icon name="store" size={18} /> Как начать
          </h2>
        </div>
        <ol className="steps">
          <li>Приходите в кафе и скажите на стойке, что хотите поработать.</li>
          <li>Оплачиваете день или пополняете счёт на сумму со скидкой.</li>
          <li>Занимаете свободный стол — и работаете до закрытия.</li>
        </ol>
        {(site?.address || site?.phone) && (
          <div className="wrap" style={{ marginTop: 16 }}>
            {site?.address && (
              <span className="chip">
                <Icon name="palm" size={15} /> {site.address}
              </span>
            )}
            {site?.phone && (
              <span className="chip">
                <Icon name="user" size={15} /> {site.phone}
              </span>
            )}
          </div>
        )}
      </section>
    </>
  );
}
