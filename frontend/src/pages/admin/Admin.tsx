import { useEffect, useMemo, useState } from "react";
import { get, patch, ApiError } from "../../api";
import type { AppTheme, Order } from "../../types";
import Icon from "../../components/Icon";
import { useAppearance } from "../../site";
import { useToast } from "../../components/ui/Toast";

// Темы продукта: ключи совпадают с SiteSettings.Theme на бэкенде.
const THEMES: { key: AppTheme; name: string; accent: string }[] = [
  { key: "neutral", name: "Нейтраль", accent: "#3557c7" },
  { key: "warm", name: "Тёплая", accent: "#9c5a1e" },
  { key: "strict", name: "Строгая", accent: "#0d7a52" },
  { key: "humu", name: "хуму", accent: "#1f58a6" },
];

// Готовые акценты «цвета заведения»; свой цвет — через пипетку рядом.
const ACCENTS = ["#3557c7", "#0d7a52", "#9c5a1e", "#b03a67", "#1f58a6", "#535a66"];

// Django-админка: на проде nginx проксирует /admin/ на бэкенд,
// в dev-режиме прокси нет — ходим на бэкенд напрямую.
const DJANGO_ADMIN_URL = import.meta.env.DEV ? "http://localhost:8000/admin/" : "/admin/";

export default function Admin() {
  const [orders, setOrders] = useState<Order[]>([]);
  const { theme, accent, set } = useAppearance();
  const notify = useToast();
  const [savingLook, setSavingLook] = useState(false);

  useEffect(() => {
    get<Order[]>("/orders/").then(setOrders).catch(() => {});
  }, []);

  // Применяем сразу (живой предпросмотр), сохраняем на сервере; при ошибке откатываем.
  async function saveAppearance(nextTheme: AppTheme, nextAccent: string) {
    const prev = { theme, accent };
    set(nextTheme, nextAccent);
    setSavingLook(true);
    try {
      await patch("/site/", { theme: nextTheme, accent_color: nextAccent });
      notify("Оформление сохранено", "ok");
    } catch (e) {
      set(prev.theme, prev.accent);
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSavingLook(false);
    }
  }

  const revenue = useMemo(
    () => orders.filter((o) => o.status !== "cancelled").reduce((s, o) => s + Number(o.total), 0),
    [orders]
  );
  // разбивка оплаченного по способу оплаты (нал/карта)
  const { cash, card } = useMemo(() => {
    const paid = orders.filter((o) => o.status === "paid");
    const sum = (m: string) =>
      paid.filter((o) => o.pay_method === m).reduce((s, o) => s + Number(o.total), 0);
    return { cash: sum("cash"), card: sum("card") };
  }, [orders]);
  const active = orders.filter((o) => o.status === "open").length;

  const stats = [
    { icon: "receipt", label: "Всего заказов", value: orders.length },
    { icon: "chart", label: "Оборот (без отмен)", value: revenue.toLocaleString("ru") },
    { icon: "cash", label: "Наличными", value: cash.toLocaleString("ru") },
    { icon: "card", label: "Картой", value: card.toLocaleString("ru") },
    { icon: "store", label: "В работе", value: active },
  ] as const;

  return (
    <>
      <h1 className="h1">Админ-панель</h1>

      <div className="grid stats stagger mt-4">
        {stats.map((s) => (
          <div className="card hover" key={s.label}>
            <span className="tx-icon"><Icon name={s.icon} size={18} /></span>
            <div className="muted mt-3">{s.label}</div>
            <div className="stat-value">
              {s.value}
            </div>
          </div>
        ))}
      </div>

      <div className="card hover enter mt-4">
        <div className="between">
          <div>
            <strong className="title">Управление каталогом</strong>
            <p className="muted subtitle">Товары и категории</p>
          </div>
          <a className="btn sm" href={DJANGO_ADMIN_URL} target="_blank" rel="noreferrer">
            Открыть <Icon name="arrowUp" size={15} />
          </a>
        </div>
      </div>

      <h2 className="section-title">Внешний вид</h2>
      <div className="card">
        <p className="muted m-0">
          Тема и акцентный цвет применяются сразу — их увидят гости и сотрудники.
        </p>
        <div className="wrap mt-3">
          {THEMES.map((t) => (
            <button
              key={t.key}
              className={"btn sm" + (theme === t.key ? "" : " ghost")}
              disabled={savingLook}
              onClick={() => saveAppearance(t.key, accent)}
            >
              <span
                className="accent-dot"
                style={{ width: 14, height: 14, background: t.accent }}
              />
              {t.name}
            </button>
          ))}
        </div>
        <div className="wrap mt-3" style={{ alignItems: "center" }}>
          <span className="muted sm">Цвет заведения:</span>
          <button
            className={"btn sm" + (accent ? " ghost" : "")}
            disabled={savingLook}
            onClick={() => saveAppearance(theme, "")}
          >
            Цвет темы
          </button>
          {ACCENTS.map((hex) => (
            <button
              key={hex}
              className={"accent-dot" + (accent.toLowerCase() === hex ? " on" : "")}
              style={{ background: hex }}
              aria-label={"Акцент " + hex}
              disabled={savingLook}
              onClick={() => saveAppearance(theme, hex)}
            />
          ))}
          <input
            type="color"
            className="input"
            style={{ width: 46, minHeight: 34, padding: "2px 4px" }}
            aria-label="Свой акцентный цвет"
            value={accent || THEMES.find((t) => t.key === theme)?.accent || "#3557c7"}
            onChange={(e) => set(theme, e.target.value)}
            onBlur={(e) => saveAppearance(theme, e.target.value)}
            disabled={savingLook}
          />
        </div>
      </div>

      <h2 className="section-title">Последние заказы</h2>
      <div className="card">
        {orders.slice(0, 20).map((o) => (
          <div className="row" key={o.id}>
            <span className="tx-icon"><Icon name="receipt" size={17} /></span>
            <div className="row-body">
              <strong>Заказ №{o.id}</strong>
              <span className="muted">{o.items.length} поз.{o.table ? ` · стол ${o.table}` : ""}</span>
            </div>
            <span className={"badge " + o.status}>{o.status_display}</span>
            <strong className="num">{o.total}</strong>
          </div>
        ))}
        {orders.length === 0 && <p className="muted">Заказов пока нет.</p>}
      </div>
    </>
  );
}
