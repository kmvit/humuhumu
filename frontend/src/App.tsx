import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { PaperBackdrop } from "./components/Ornaments";
import Login from "./pages/Login";
import Coworking from "./pages/Coworking";
import Menu from "./pages/client/Menu";
import Orders from "./pages/client/Orders";
import Wallet from "./pages/client/Wallet";
import Cashier from "./pages/cashier/Cashier";
import Admin from "./pages/admin/Admin";
import type { Role } from "./types";

const HOME_BY_ROLE: Record<Role, string> = {
  client: "/client",
  cashier: "/cashier",
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
        {user?.role === "client" && (
          <>
            <Route path="/client" element={<Menu />} />
            <Route path="/client/orders" element={<Orders />} />
            <Route path="/client/wallet" element={<Wallet />} />
          </>
        )}
        {user?.role === "cashier" && <Route path="/cashier" element={<Cashier />} />}
        {user?.role === "admin" && <Route path="/admin" element={<Admin />} />}
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>
      </Routes>
    </>
  );
}
