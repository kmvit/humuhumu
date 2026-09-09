import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { useSite, useFeature } from "../site";
import { ApiError, post } from "../api";
import Icon from "../components/Icon";

export default function Login() {
  const { login, register } = useAuth();
  const site = useSite();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [birth, setBirth] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [registered, setRegistered] = useState(false);
  // Когда бонусы включены, регистрация сразу заводит гостя в программу
  // и начисляет приветственные — отдельным шагом их пришлось бы
  // выпрашивать у гостя второй раз.
  const bonusOn = useFeature("loyalty") && !!site?.bonus_enabled;
  const welcome = site?.bonus_welcome ?? 0;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") {
        await login(username, password);
      } else if (bonusOn) {
        await post("/loyalty/enroll/", {
          name,
          phone,
          ...(birth ? { birth_date: birth } : {}),
        });
        setRegistered(true);
      } else {
        await register(name, phone);
        setRegistered(true);
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : mode === "login"
            ? "Не удалось войти"
            : "Не удалось зарегистрироваться"
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="container"
      style={{ maxWidth: 400, minHeight: "100dvh", display: "grid", placeItems: "center" }}
    >
      <div className="card enter" style={{ width: "100%", padding: 26 }}>
        <div style={{ textAlign: "center", marginBottom: 22 }}>
          <span
            className="logo"
            style={{ width: 56, height: 56, borderRadius: 18, margin: "0 auto 14px" }}
          >
            {site?.logo ? <img src={site.logo} alt="" /> : <Icon name="coffee" size={30} />}
          </span>
          <h1 className="h1">{site?.name ?? "Добро пожаловать"}</h1>
          {site?.tagline && (
            <p className="script" style={{ fontSize: 22, color: "var(--brand-2)", margin: "2px 0 0" }}>
              {site.tagline}
            </p>
          )}
          <Link to="/" className="navlink" style={{ marginTop: 12 }}>
            <Icon name="coffee" size={16} /> Посмотреть меню
          </Link>
        </div>

        <div className="wrap" style={{ background: "var(--brand-soft)", padding: 4, borderRadius: 13, marginBottom: 18 }}>
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => { setMode(m); setError(null); setRegistered(false); }}
              className={"btn sm " + (mode === m ? "" : "ghost")}
              style={{ flex: 1, boxShadow: mode === m ? undefined : "none", background: mode === m ? undefined : "transparent", border: "none" }}
            >
              {m === "login" ? "Вход" : "Регистрация"}
            </button>
          ))}
        </div>

        {registered ? (
          <div className="enter" style={{ textAlign: "center" }}>
            <Icon name="check" size={30} />
            <p style={{ margin: "10px 0 0" }}>
              Спасибо, {name}! Логин и пароль пришлём на {phone}.
            </p>
            {bonusOn && welcome > 0 && (
              <p className="muted" style={{ margin: "8px 0 0" }}>
                Начислили {welcome.toLocaleString("ru")} приветственных бонусов ·
                1 бонус = 1 ₽
              </p>
            )}
            <button
              type="button"
              className="btn ghost block"
              style={{ marginTop: 16 }}
              onClick={() => { setName(""); setPhone(""); setBirth(""); setRegistered(false); }}
            >
              Зарегистрировать ещё одного гостя
            </button>
          </div>
        ) : (
          <form onSubmit={submit}>
            {mode === "register" ? (
              <>
                <div className="field">
                  <label className="label">Имя</label>
                  <input className="input" value={name} onChange={(e) => setName(e.target.value)} required autoComplete="given-name" placeholder="Как вас зовут" />
                </div>
                <div className="field">
                  <label className="label">Телефон</label>
                  <input className="input" value={phone} onChange={(e) => setPhone(e.target.value)} required inputMode="tel" autoComplete="tel" placeholder="+7 999 000-00-00" />
                </div>
                {bonusOn && (
                  <div className="field">
                    <label className="label">Дата рождения</label>
                    <input className="input" type="date" value={birth} onChange={(e) => setBirth(e.target.value)} autoComplete="bday" />
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="field">
                  <label className="label">Логин</label>
                  <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" />
                </div>
                <div className="field">
                  <label className="label">Пароль</label>
                  <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
                </div>
              </>
            )}
            {error && <p className="error" role="alert">{error}</p>}
            <button type="submit" className="btn block" disabled={busy} style={{ marginTop: 6 }}>
              {busy ? <Icon name="spark" size={18} /> : <Icon name="check" size={18} />}
              {mode === "login" ? "Войти" : "Зарегистрироваться"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
