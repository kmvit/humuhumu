import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { PaperBackdrop } from "./components/Ornaments";
import Login from "./pages/Login";
import Coworking from "./pages/Coworking";
import Menu from "./pages/client/Menu";
import MenuReels from "./pages/client/MenuReels";
import Waiter from "./pages/waiter/Waiter";
import Kitchen from "./pages/kitchen/Kitchen";
import Bar from "./pages/bar/Bar";
import Admin from "./pages/admin/Admin";
import Warehouse from "./pages/warehouse/Warehouse";
import Offer from "./pages/legal/Offer";
import Privacy from "./pages/legal/Privacy";
import Payment from "./pages/legal/Payment";
import Contacts from "./pages/legal/Contacts";
import type { Role } from "./types";

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

  const orbs = <PaperBackdrop />;

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
      {/* экспериментальное меню Reels — полноэкранное, без общего Layout */}
      <Route path="/reels" element={<MenuReels />} />
      <Route element={<Layout />}>
        {/* коворкинг — информационная страница, открыта всем */}
        <Route path="/coworking" element={<Coworking />} />
        {/* юридические страницы — открыты всем */}
        <Route path="/offer" element={<Offer />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/payment" element={<Payment />} />
        <Route path="/contacts" element={<Contacts />} />
        {!user && <Route path="/" element={<Menu />} />}
        {user?.role === "client" && <Route path="/client" element={<Menu />} />}
        {user?.role === "waiter" && <Route path="/waiter" element={<Waiter />} />}
        {user?.role === "cook" && <Route path="/kitchen" element={<Kitchen />} />}
        {user?.role === "bar" && <Route path="/bar" element={<Bar />} />}
        {user?.role === "warehouse" && <Route path="/warehouse" element={<Warehouse />} />}
        {user?.role === "admin" && <Route path="/admin" element={<Admin />} />}
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>
      </Routes>
    </>
  );
}
