import { useState } from "react";
import { patch, ApiError } from "../../api";
import type { AppTheme } from "../../types";
import Icon from "../../components/Icon";
import { useAppearance, useSite } from "../../site";
import { useToast } from "../../components/ui/Toast";

// Темы продукта: ключи совпадают с SiteSettings.Theme на бэкенде.
const THEMES: { key: AppTheme; name: string; accent: string }[] = [
  { key: "neutral", name: "Нейтраль", accent: "#3557c7" },
  { key: "warm", name: "Тёплая", accent: "#9c5a1e" },
  { key: "strict", name: "Строгая", accent: "#0d7a52" },
  { key: "island", name: "Островная", accent: "#1f58a6" },
  { key: "padacha", name: "Падача", accent: "#d2570a" },
];

// Готовые акценты «цвета заведения»; свой цвет — через пипетку рядом.
const ACCENTS = ["#3557c7", "#0d7a52", "#9c5a1e", "#b03a67", "#1f58a6", "#535a66"];

/** Раздел «Оформление»: тема, режим для гостя и цвет заведения. */
export default function Appearance() {
  const { theme, accent, set } = useAppearance();
  const site = useSite();
  const [dark, setDark] = useState<boolean | null>(null);
  const [savingMode, setSavingMode] = useState(false);
  // Локальное состояние перекрывает контекст: он загружается один раз при
  // старте и после сохранения показывал бы старое значение.
  const darkDefault = dark ?? site?.dark_by_default ?? false;
  const notify = useToast();
  const [savingLook, setSavingLook] = useState(false);

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

  return (
    <>
      <h2 className="section-title">Оформление</h2>
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

    </>
  );
}
