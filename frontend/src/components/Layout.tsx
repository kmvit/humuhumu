import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { useSite } from "../site";
import { useTheme } from "../theme";
import Footer from "./Footer";
import Icon, { type IconName } from "./Icon";
import InstallPWA from "./InstallPWA";

const NAV: Record<string, { to: string; label: string; icon: IconName }[]> = {
  client: [
    { to: "/client", label: "Меню", icon: "coffee" },
  ],
  waiter: [
    { to: "/waiter", label: "Столы", icon: "store" },
    { to: "/shifts", label: "Смены", icon: "user" },
  ],
  cook: [
    { to: "/kitchen", label: "Кухня", icon: "sandwich" },
    { to: "/shifts", label: "Смены", icon: "user" },
  ],
  bar: [
    { to: "/bar", label: "Бар", icon: "coffee" },
    { to: "/shifts", label: "Смены", icon: "user" },
  ],
  warehouse: [
    { to: "/warehouse", label: "Склад", icon: "box" },
    { to: "/shifts", label: "Смены", icon: "user" },
    { to: "/finance", label: "Финансы", icon: "wallet" },
  ],
  admin: [
    { to: "/admin", label: "Админ", icon: "chart" },
    { to: "/shifts", label: "Смены", icon: "user" },
    { to: "/finance", label: "Финансы", icon: "wallet" },
  ],
  guest: [
    { to: "/", label: "Меню", icon: "coffee" },
  ],
};

// Планшетные рабочие роли: у них шапка сжата в тонкую панель, а подвал убран,
// чтобы доска целиком помещалась на экран планшета.
const STAFF_ROLES = ["waiter", "cook", "bar", "warehouse"];

export default function Layout() {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const site = useSite();
  const navigate = useNavigate();
  const staff = !!user && STAFF_ROLES.includes(user.role);
  const counter = site?.service_mode === "counter";
  const links = (NAV[user?.role ?? "guest"] ?? []).map((l) =>
    // в режиме стойки у официанта не столы, а очередь заказов
    counter && l.to === "/waiter" ? { ...l, label: "Стойка", icon: "receipt" as const } : l
  );

  const themeBtn = (
    <button
      className="icon-btn"
      onClick={toggle}
      aria-label={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
    >
      <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
    </button>
  );
  const authBtn = user ? (
    <button className="icon-btn" onClick={logout} aria-label="Выйти">
      <Icon name="logout" size={18} />
    </button>
  ) : (
    <button className="btn sm" onClick={() => navigate("/login")}>
      Войти
    </button>
  );

  return (
    <>
      <header className={"header" + (staff ? " slim" : "")}>
        <div className="header-inner">
          <nav>
            {links.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.to === "/client" || l.to === "/"}
                className={({ isActive }) => "navlink" + (isActive ? " active" : "")}
              >
                <Icon name={l.icon} size={17} />
                <span className="hide-sm">{l.label}</span>
              </NavLink>
            ))}
          </nav>

          {/* логотип по центру — только в полной шапке, не на планшете персонала */}
          {!staff && (
            <span className="brand">
              <span className="logo">
                {site?.logo ? <img src={site.logo} alt="" /> : <Icon name="coffee" size={20} />}
              </span>
              <span className="brand-name hide-sm">{site?.name ?? "Кафе"}</span>
            </span>
          )}

          <div className="header-actions">
            {user?.balance !== null && user?.balance !== undefined && (
              <span className="chip">
                <Icon name="spark" size={15} />
                <span className="num">{Number(user.balance).toLocaleString("ru")}</span>
              </span>
            )}

            {!staff && <InstallPWA />}
            {themeBtn}
            {authBtn}
          </div>
        </div>
      </header>
      <main className={"container" + (staff ? " staff" : "")}>
        <Outlet />
      </main>
      {!staff && <Footer />}
    </>
  );
}
