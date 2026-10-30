import { useState } from "react";
import { ApiError } from "../api";
import { useAuth } from "../auth";
import Icon from "./Icon";
import Modal from "./ui/Modal";

type DemoAccount = { username: string; label: string; password: string };

export default function DemoAccess({
  accounts,
  placement,
}: {
  accounts: DemoAccount[];
  placement: "header" | "login";
}) {
  const { login } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  if (!accounts.length) return null;

  async function enter(account: DemoAccount) {
    setBusy(account.username);
    setError("");
    try {
      await login(account.username, account.password);
      setOpen(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось войти в демо");
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      {placement === "header" ? (
        <button className="demo-access-trigger" onClick={() => { setError(""); setOpen(true); }}>
          Демо-доступы
        </button>
      ) : (
        <section className="demo-access-panel" aria-label="Демо-доступы">
          <div className="demo-access-panel-head">
            <strong>Попробовать демо</strong>
            <span>Выберите роль для входа</span>
          </div>
          <div className="demo-access-roles">
            {accounts.map((account) => (
              <button
                type="button"
                className="btn sm ghost"
                key={account.username}
                disabled={!!busy}
                onClick={() => enter(account)}
              >
                {busy === account.username ? <Icon name="spark" size={15} /> : account.label}
              </button>
            ))}
          </div>
          <span className="demo-access-password">Общий пароль: <strong>{accounts[0].password}</strong></span>
          {error && <p className="error" role="alert">{error}</p>}
        </section>
      )}

      {open && placement === "header" && (
        <Modal onClose={() => setOpen(false)} head={<strong>Войти в демо</strong>}>
          <p className="muted mt-0">Выберите роль. Вход выполнится автоматически.</p>
          <div className="demo-access-list">
            {accounts.map((account) => (
              <button
                className="demo-access-account"
                key={account.username}
                disabled={!!busy}
                onClick={() => enter(account)}
              >
                <span className="demo-access-account-copy">
                  <strong>{account.label}</strong>
                  <span>{account.username} · {account.password}</span>
                </span>
                <span className="demo-access-account-action">
                  {busy === account.username ? <Icon name="spark" size={16} /> : <Icon name="arrowRight" size={17} />}
                </span>
              </button>
            ))}
          </div>
          {error && <p className="error" role="alert">{error}</p>}
        </Modal>
      )}
    </>
  );
}
