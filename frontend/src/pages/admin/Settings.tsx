import type { ReactNode } from "react";
import { Navigate, NavLink, useParams } from "react-router-dom";
import Icon, { type IconName } from "../../components/Icon";
import Appearance from "./Appearance";
import Kassa from "./Kassa";
import OnlinePayment from "./OnlinePayment";
import OrderingPause from "./OrderingPause";
import ServiceFormat from "./ServiceFormat";
import Staff from "./Staff";
import Subscription from "./Subscription";
import VenueProfile from "./VenueProfile";

/** «Настройки» заведения: по разделу на экран.

    Раньше всё это шло одной лентой под цифрами админки — чтобы добраться
    до сотрудников, проматывали оформление. Теперь у каждого раздела свой
    адрес (/settings/staff), и на него можно сослаться из «Сводки».
*/
const SECTIONS: { key: string; label: string; note: string; icon: IconName; body: () => ReactNode }[] = [
  { key: "venue", label: "Заведение", note: "Название, контакты, реквизиты", icon: "store",
    body: () => <VenueProfile /> },
  { key: "ordering", label: "Заказы с сайта", note: "Технический перерыв", icon: "receipt",
    body: () => <OrderingPause /> },
  { key: "look", label: "Оформление", note: "Тема и цвет", icon: "spark",
    body: () => <Appearance /> },
  { key: "payments", label: "Оплата и касса", note: "Эквайринг, онлайн-касса", icon: "card",
    body: () => (
      <>
        <OnlinePayment />
        <Kassa />
      </>
    ) },
  { key: "staff", label: "Сотрудники", note: "Входы и роли", icon: "user",
    body: () => <Staff /> },
  { key: "subscription", label: "Подписка", note: "Тариф и формат работы", icon: "wallet",
    body: () => (
      <>
        <Subscription />
        <ServiceFormat />
      </>
    ) },
];

export default function Settings() {
  const { section } = useParams();
  const current = SECTIONS.find((s) => s.key === section);
  if (!current) return <Navigate to={`/settings/${SECTIONS[0].key}`} replace />;

  return (
    <>
      <h1 className="h1">Настройки</h1>
      <div className="settings mt-4">
        <nav className="settings-nav" aria-label="Разделы настроек">
          {SECTIONS.map((s) => (
            <NavLink
              key={s.key}
              to={`/settings/${s.key}`}
              className={({ isActive }) => "settings-link" + (isActive ? " active" : "")}
            >
              <span className="settings-ico">
                <Icon name={s.icon} size={17} />
              </span>
              <span className="settings-link-text">
                <span>{s.label}</span>
                <span className="muted sm">{s.note}</span>
              </span>
            </NavLink>
          ))}
        </nav>
        {/* у каждого раздела свой заголовок section-title — отдельный не нужен */}
        <section className="settings-body">{current.body()}</section>
      </div>
    </>
  );
}
