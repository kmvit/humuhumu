import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { PaperBackdrop } from "./components/Ornaments";
import Login from "./pages/Login";
import Coworking from "./pages/Coworking";
import Menu from "./pages/client/Menu";
import Waiter from "./pages/waiter/Waiter";
import Kitchen from "./pages/kitchen/Kitchen";
import Bar from "./pages/bar/Bar";
import Admin from "./pages/admin/Admin";
import type { Role } from "./types";

const HOME_BY_ROLE: Record<Role, string> = {
  client: "/client",
  waiter: "/waiter",
  cook: "/kitchen",
  bar: "/bar",
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
      <Route element={<Layout />}>
        {/* коворкинг — информационная страница, открыта всем */}
        <Route path="/coworking" element={<Coworking />} />
        {!user && <Route path="/" element={<Menu />} />}
        {user?.role === "client" && <Route path="/client" element={<Menu />} />}
        {user?.role === "waiter" && <Route path="/waiter" element={<Waiter />} />}
        {user?.role === "cook" && <Route path="/kitchen" element={<Kitchen />} />}
        {user?.role === "bar" && <Route path="/bar" element={<Bar />} />}
        {user?.role === "admin" && <Route path="/admin" element={<Admin />} />}
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>
      </Routes>
    </>
  );
}
