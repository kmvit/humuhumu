import { useEffect, useState } from "react";
import { get, patch, post, ApiError } from "../../api";
import Icon from "../../components/Icon";
import Modal from "../../components/ui/Modal";
import { useToast } from "../../components/ui/Toast";
import type { Role, StaffMember } from "../../types";

/** Раздел «Сотрудники» в панели владельца.
 *
 * Смысл раздела — чтобы заведению не выдавать Django-админку: логины
 * персоналу владелец заводит сам. Уволенные не удаляются, а прячутся под
 * переключателем: у человека остаются смены и заказы.
 */

const ROLES: { key: StaffRole; name: string; note: string }[] = [
  { key: "waiter", name: "Официант", note: "Столы, заказы, оплата" },
  { key: "cook", name: "Повар", note: "Экран кухни" },
  { key: "bar", name: "Бар", note: "Экран бара" },
  { key: "warehouse", name: "Менеджер", note: "Склад, смены, финансы" },
  { key: "admin", name: "Администратор", note: "Всё, включая эту страницу" },
];

type StaffRole = Exclude<Role, "client">;
type Draft = { username: string; name: string; role: StaffRole; password: string };

const EMPTY: Draft = { username: "", name: "", role: "waiter", password: "" };

export default function Staff() {
  const [people, setPeople] = useState<StaffMember[]>([]);
  const [showDismissed, setShowDismissed] = useState(false);
  const [form, setForm] = useState<Draft | null>(null);
  const [pwFor, setPwFor] = useState<StaffMember | null>(null);
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const notify = useToast();

  const load = () =>
    get<StaffMember[]>("/staff/")
      .then(setPeople)
      .catch(() => notify("Не удалось загрузить сотрудников", "bad"));

  useEffect(() => {
    load();
  }, []);

  // DRF отдаёт ошибки валидации полями: {"password": ["пароль короткий"]}, и
  // api.ts кладёт их в message как JSON. Владельцу нужен текст, а не JSON.
  const fail = (e: unknown) => {
    let text = "Не получилось, попробуйте ещё раз";
    if (e instanceof ApiError) {
      text = e.message;
      try {
        const fields = JSON.parse(e.message) as Record<string, string[] | string>;
        const messages = Object.values(fields).flat();
        if (messages.length) text = messages.join(" ");
      } catch {
        /* не JSON — показываем как есть */
      }
    }
    notify(text, "bad");
  };

  const create = () => {
    if (!form) return;
    setSaving(true);
    post<StaffMember>("/staff/", form)
      .then(() => {
        notify(`${form.username}: доступ создан`, "ok");
        setForm(null);
        load();
      })
      .catch(fail)
      .finally(() => setSaving(false));
  };

  const changePassword = () => {
    if (!pwFor) return;
    setSaving(true);
    patch<StaffMember>(`/staff/${pwFor.id}/`, { password })
      .then(() => {
        notify(`${pwFor.username}: пароль изменён`, "ok");
        setPwFor(null);
        setPassword("");
      })
      .catch(fail)
      .finally(() => setSaving(false));
  };

  const toggle = (person: StaffMember) => {
    const action = person.is_active ? "dismiss" : "restore";
    post<StaffMember>(`/staff/${person.id}/${action}/`, {})
      .then(() => {
        notify(person.is_active ? "Сотрудник уволен" : "Сотрудник восстановлен", "ok");
        load();
      })
      .catch(fail);
  };

  const visible = people.filter((p) => showDismissed || p.is_active);
  const dismissed = people.length - people.filter((p) => p.is_active).length;

  return (
    <>
      <h2 className="section-title">Сотрудники</h2>
      <div className="card">
        <div className="between">
          <p className="muted m-0">
            Логины для персонала. Уволенный не может войти, но его смены и
            заказы остаются в отчётах.
          </p>
          <button className="btn sm" onClick={() => setForm({ ...EMPTY })}>
            <Icon name="user" size={15} /> Добавить
          </button>
        </div>

        <div className="mt-3">
          {visible.map((p) => (
            <div key={p.id} className="row">
              <div>
                <strong>{p.name || p.username}</strong>
                <span className="muted"> · {p.role_display}</span>
                <div className="muted subtitle">
                  логин: {p.username}
                  {!p.is_active && " · уволен"}
                </div>
              </div>
              <div className="wrap">
                <button
                  className="btn sm ghost"
                  onClick={() => {
                    setPwFor(p);
                    setPassword("");
                  }}
                >
                  Пароль
                </button>
                <button className="btn sm ghost" onClick={() => toggle(p)}>
                  {p.is_active ? "Уволить" : "Вернуть"}
                </button>
              </div>
            </div>
          ))}
          {!visible.length && <p className="muted">Пока никого нет</p>}
        </div>

        {dismissed > 0 && (
          <button
            className="btn sm ghost mt-3"
            onClick={() => setShowDismissed((v) => !v)}
          >
            {showDismissed ? "Скрыть уволенных" : `Показать уволенных (${dismissed})`}
          </button>
        )}
      </div>

      {form && (
        <Modal onClose={() => setForm(null)} head={<strong>Новый сотрудник</strong>}>
          <label className="field">
            <span className="label">Имя</span>
            <input
              className="input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Как показывать в сменах"
            />
          </label>
          <label className="field">
            <span className="label">Логин</span>
            <input
              className="input"
              value={form.username}
              autoCapitalize="none"
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              placeholder="Например, anna"
            />
          </label>
          {/* Пароль намеренно виден: владелец сам придумывает его и диктует
              сотруднику, прятать точками тут нечего и незачем. */}
          <label className="field">
            <span className="label">Пароль</span>
            <input
              className="input"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              placeholder="Не короче 8 символов"
            />
          </label>
          <div className="field">
            <span className="label">Роль</span>
            <div className="grid cols-2 mt-2">
              {ROLES.map((r) => (
                <button
                  key={r.key}
                  className={
                    "card mode-tile" + (form.role === r.key ? " active" : " hover")
                  }
                  onClick={() => setForm({ ...form, role: r.key })}
                >
                  <strong>{r.name}</strong>
                  <span className="muted">{r.note}</span>
                </button>
              ))}
            </div>
          </div>
          <button className="btn mt-3" disabled={saving} onClick={create}>
            {saving ? "Создаём…" : "Создать доступ"}
          </button>
        </Modal>
      )}

      {pwFor && (
        <Modal
          onClose={() => setPwFor(null)}
          head={<strong>Пароль для «{pwFor.name || pwFor.username}»</strong>}
        >
          <label className="field">
            <span className="label">Новый пароль</span>
            <input
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Не короче 8 символов"
            />
          </label>
          <button className="btn mt-3" disabled={saving} onClick={changePassword}>
            {saving ? "Сохраняем…" : "Сохранить"}
          </button>
        </Modal>
      )}
    </>
  );
}
