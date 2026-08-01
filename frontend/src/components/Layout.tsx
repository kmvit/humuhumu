import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { useSite } from "../site";
import { useTheme } from "../theme";
import Footer from "./Footer";
import Icon, { type IconName } from "./Icon";

const NAV: Record<string, { to: string; label: string; icon: IconName }[]> = {
  client: [
    { to: "/client", label: "Меню", icon: "coffee" },
    { to: "/coworking", label: "Коворкинг", icon: "laptop" },
  ],
  waiter: [
    { to: "/waiter", label: "Новый заказ", icon: "receipt" },
    { to: "/coworking", label: "Коворкинг", icon: "laptop" },
  ],
  cook: [
    { to: "/kitchen", label: "Кухня", icon: "sandwich" },
    { to: "/coworking", label: "Коворкинг", icon: "laptop" },
  ],
  cashier: [
    { to: "/cashier", label: "Касса-бар", icon: "store" },
    { to: "/coworking", label: "Коворкинг", icon: "laptop" },
  ],
  admin: [
    { to: "/admin", label: "Админ", icon: "chart" },
    { to: "/coworking", label: "Коворкинг", icon: "laptop" },
  ],
  guest: [
    { to: "/", label: "Меню", icon: "coffee" },
    { to: "/coworking", label: "Коворкинг", icon: "laptop" },
  ],
};

export default function Layout() {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const site = useSite();
  const navigate = useNavigate();
  const links = NAV[user?.role ?? "guest"] ?? [];

  return (
    <>
      <header className="header">
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

          {/* логотип по центру шапки */}
          <span className="brand">
            <span className="logo">
              {site?.logo ? <img src={site.logo} alt="" /> : <Icon name="coffee" size={20} />}
            </span>
            <span className="brand-name hide-sm">{site?.name ?? "Кафе"}</span>
          </span>

          <div className="header-actions">
            {user?.balance !== null && user?.balance !== undefined && (
              <span className="chip">
                <Icon name="spark" size={15} />
                <span className="num">{Number(user.balance).toLocaleString("ru")}</span>
              </span>
            )}

            <button
              className="icon-btn"
              onClick={toggle}
              aria-label={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
            >
              <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
            </button>
            {user ? (
              <button className="icon-btn" onClick={logout} aria-label="Выйти">
                <Icon name="logout" size={18} />
              </button>
            ) : (
              <button className="btn sm" onClick={() => navigate("/login")}>
                Войти
              </button>
            )}
          </div>
        </div>
      </header>
      <main className="container">
        <Outlet />
      </main>
      <Footer />
    </>
  );
}
