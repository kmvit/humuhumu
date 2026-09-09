import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { useAppearance, useFeature, useSite } from "./site";
import Layout from "./components/Layout";
import { PaperBackdrop } from "./components/Ornaments";
import Login from "./pages/Login";
import Menu from "./pages/client/Menu";
import MenuReels from "./pages/client/MenuReels";
import Waiter from "./pages/waiter/Waiter";
import Counter from "./pages/counter/Counter";
import Kitchen from "./pages/kitchen/Kitchen";
import Bar from "./pages/bar/Bar";
import Admin from "./pages/admin/Admin";
import Warehouse from "./pages/warehouse/Warehouse";
import Shifts from "./pages/shifts/Shifts";
import Finance from "./pages/finance/Finance";
import Offer from "./pages/legal/Offer";
import Privacy from "./pages/legal/Privacy";
import Payment from "./pages/legal/Payment";
import Contacts from "./pages/legal/Contacts";
import type { Feature, Role } from "./types";

// Экран вместо раздела, которого нет в тарифе заведения. Ссылки в шапке
// спрятаны, но прямой заход по адресу (или закладка) должен объяснить,
// а не зациклить редирект. Сам запрет держат permissions на бэке.
function PlanLocked({ what, need }: { what: string; need: string }) {
  return (
    <div style={{ textAlign: "center", padding: "56px 16px" }}>
      <h2 style={{ marginBottom: 8 }}>{what}</h2>
      <p style={{ opacity: 0.7 }}>Доступно на тарифе «{need}». Подключение — через «Падачу».</p>
    </div>
  );
}

function Gated({
  feature,
  what,
  need,
  children,
}: {
  feature: Feature;
  what: string;
  need: string;
  children: JSX.Element;
}) {
  return useFeature(feature) ? children : <PlanLocked what={what} need={need} />;
}

const HOME_BY_ROLE: Record<Role, string> = {
  client: "/client",
  waiter: "/waiter",
  cook: "/kitchen",
  bar: "/bar",
  warehouse: "/warehouse",
  admin: "/admin",
};

export default function App() {
  const { user, loading } = useAuth();
  const { theme } = useAppearance();
  // Формат заведения: в «стойке» нет столов и официанта — заказ собирает
  // и выдаёт один человек, поэтому у роли официанта другой экран.
  const counter = useSite()?.service_mode === "counter";

  // фирменный декор (штриховка, пальмы) — только в «Островной» теме
  const orbs = theme === "island" ? <PaperBackdrop /> : null;

  if (loading)
    return (
      <>
        {orbs}
        <div className="container">Загрузка…</div>
      </>
    );
  // гость без входа попадает сразу в меню
  const home = user ? HOME_BY_ROLE[user.role] : "/";

  return (
    <>
      {orbs}
      <Routes>
      <Route
        path="/login"
        element={user ? <Navigate to={home} replace /> : <Login />}
      />
      {/* Меню лентой — полноэкранное, без общего Layout. Оно же главная для
          гостя: лента с фото продаёт блюда лучше списка, а список остаётся
          на /menu и доступен из ленты в один тап. Именно Route, а не редирект
          с "/" — иначе потеряется ?table из QR-кода на столе. */}
      <Route path="/reels" element={<MenuReels />} />
      {!user && <Route path="/" element={<MenuReels />} />}
      <Route element={<Layout />}>
        {/* юридические страницы — открыты всем */}
        <Route path="/offer" element={<Offer />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/payment" element={<Payment />} />
        <Route path="/contacts" element={<Contacts />} />
        {!user && <Route path="/menu" element={<Menu />} />}
        {user?.role === "client" && <Route path="/client" element={<Menu />} />}
        {user?.role === "waiter" && (
          <Route path="/waiter" element={counter ? <Counter /> : <Waiter />} />
        )}
        {user?.role === "cook" && (
          <Route
            path="/kitchen"
            element={<Gated feature="stations" what="Экран кухни" need="Зал"><Kitchen /></Gated>}
          />
        )}
        {user?.role === "bar" && (
          <Route
            path="/bar"
            element={<Gated feature="stations" what="Экран бара" need="Зал"><Bar /></Gated>}
          />
        )}
        {user?.role === "warehouse" && (
          <Route
            path="/warehouse"
            element={<Gated feature="inventory" what="Склад" need="Максимум"><Warehouse /></Gated>}
          />
        )}
        {user?.role === "admin" && <Route path="/admin" element={<Admin />} />}
        {/* смены — всем сотрудникам, клиентам не нужно */}
        {user && user.role !== "client" && (
          <Route
            path="/shifts"
            element={<Gated feature="shifts" what="Смены" need="Максимум"><Shifts /></Gated>}
          />
        )}
        {/* финансы — управленческий раздел: ведомость и деньги заведения */}
        {(user?.role === "warehouse" || user?.role === "admin") && (
          <Route
            path="/finance"
            element={<Gated feature="finance" what="Финансы" need="Максимум"><Finance /></Gated>}
          />
        )}
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>
      </Routes>
    </>
  );
}
