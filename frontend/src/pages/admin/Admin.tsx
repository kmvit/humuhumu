import { useEffect, useMemo, useState } from "react";
import { get, patch, ApiError } from "../../api";
import type { AppTheme, Order } from "../../types";
import Icon, { type IconName } from "../../components/Icon";
import { useAppearance, useSite } from "../../site";
import { useToast } from "../../components/ui/Toast";
import Staff from "./Staff";
import Bonuses from "./Bonuses";
import OnlinePayment from "./OnlinePayment";
import Subscription from "./Subscription";

// Темы продукта: ключи совпадают с SiteSettings.Theme на бэкенде.
// Формат обслуживания: зал со столами или стойка с выдачей по номеру.
// Владельцу только показываем — переключение живёт в Django-админке.
const MODES: Record<"hall" | "counter", { name: string; note: string; icon: IconName }> = {
  hall: {
    name: "Зал с официантами",
    note: "Гость за столом, официант принимает и подаёт",
    icon: "store",
  },
  counter: {
    name: "Стойка и окно",
    note: "Заказ по QR, выдача по номеру, без столов",
    icon: "receipt",
  },
};

const THEMES: { key: AppTheme; name: string; accent: string }[] = [
  { key: "neutral", name: "Нейтраль", accent: "#3557c7" },
  { key: "warm", name: "Тёплая", accent: "#9c5a1e" },
  { key: "strict", name: "Строгая", accent: "#0d7a52" },
  { key: "island", name: "Островная", accent: "#1f58a6" },
  { key: "padacha", name: "Падача", accent: "#d2570a" },
];

// Готовые акценты «цвета заведения»; свой цвет — через пипетку рядом.
const ACCENTS = ["#3557c7", "#0d7a52", "#9c5a1e", "#b03a67", "#1f58a6", "#535a66"];

export default function Admin() {
  const [orders, setOrders] = useState<Order[]>([]);
  const { theme, accent, set } = useAppearance();
  const site = useSite();
  const [dark, setDark] = useState<boolean | null>(null);
  const [savingMode, setSavingMode] = useState(false);
  // Локальное состояние перекрывает контекст: он загружается один раз при
  // старте и после сохранения показывал бы старое значение.
  const serviceMode = site?.service_mode ?? "hall";
  const darkDefault = dark ?? site?.dark_by_default ?? false;
  const notify = useToast();
  const [savingLook, setSavingLook] = useState(false);

  useEffect(() => {
    get<Order[]>("/orders/").then(setOrders).catch(() => {});
  }, []);

  /** Каким режимом встречать гостя, пока он сам не переключил. */
  async function saveDark(value: boolean) {
    setSavingMode(true);
    try {
      await patch("/site/", { dark_by_default: value });
      setDark(value);
      notify(value ? "По умолчанию тёмная" : "По умолчанию светлая", "ok");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Не удалось сохранить", "bad");
    } finally {
      setSavingMode(false);
    }
  }

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

  // Копейки в сводке не нужны и только удлиняют семизначные суммы: сложение
  // Number(total) вдобавок даёт хвост вроде 2 901 160,0000001.
  const money = (v: number) => Math.round(v).toLocaleString("ru");

  const stats = [
    { icon: "receipt", label: "Всего заказов", value: orders.length },
    { icon: "chart", label: "Оборот (без отмен)", value: money(revenue) },
    { icon: "cash", label: "Наличными", value: money(cash) },
    { icon: "card", label: "Картой", value: money(card) },
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

      <Subscription />

      <h2 className="section-title">Формат работы</h2>
      <div className="card">
        <div className="between">
          <div className="inline">
            <span className="tx-icon">
              <Icon name={MODES[serviceMode].icon} size={18} />
            </span>
            <div>
              <strong className="title">{MODES[serviceMode].name}</strong>
              <p className="muted subtitle m-0">{MODES[serviceMode].note}</p>
            </div>
          </div>
        </div>
        {/* Переключение убрано намеренно: смена формата разом меняет экраны
            всем — официанту, кухне, гостю. Это настройка запуска, а не
            кнопка на каждый день; меняется при подключении заведения. */}
        <p className="muted sm mt-3 m-0">
          Формат выбирается при подключении заведения. Чтобы сменить —
          напишите в поддержку «Падачи».
        </p>
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
          <span className="muted sm">Гость по умолчанию видит:</span>
          <button
            className={"btn sm" + (darkDefault ? " ghost" : "")}
            disabled={savingMode}
            onClick={() => saveDark(false)}
          >
            <Icon name="sun" size={15} /> Светлую
          </button>
          <button
            className={"btn sm" + (darkDefault ? "" : " ghost")}
            disabled={savingMode}
            onClick={() => saveDark(true)}
          >
            <Icon name="moon" size={15} /> Тёмную
          </button>
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

      <OnlinePayment />

      <Bonuses />

      <Staff />

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
