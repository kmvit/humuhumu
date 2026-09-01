import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

// Действующая тема — её читает анти-FOUC скрипт в index.html.
const KEY = "theme";
// Осознанный выбор гостя. Отдельно от KEY, иначе не отличить «человек нажал
// кнопку» от «мы записали значение по умолчанию» — и настройка заведения
// никогда бы не применилась.
const CHOICE = "theme_choice";
// Кэш настройки заведения: со второго захода тема верна ещё до ответа API.
const DEFAULT = "theme_default";
const EVT = "app-theme-default";

function read(key: string): Theme | null {
  try {
    const v = localStorage.getItem(key);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null; // приватный режим — просто без запоминания
  }
}

function initial(): Theme {
  return read(CHOICE) ?? read(DEFAULT) ?? "light";
}

export function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

/** Тема заведения по умолчанию — приходит из /api/site/ (см. site.tsx). */
export function applySiteDefault(dark: boolean) {
  const value: Theme = dark ? "dark" : "light";
  try {
    localStorage.setItem(DEFAULT, value);
  } catch {
    /* приватный режим */
  }
  // Гость, который уже выбрал тему сам, ничего не заметит.
  if (!read(CHOICE)) window.dispatchEvent(new CustomEvent(EVT, { detail: value }));
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initial);

  useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* приватный режим */
    }
  }, [theme]);

  useEffect(() => {
    const onDefault = (e: Event) => setTheme((e as CustomEvent).detail as Theme);
    window.addEventListener(EVT, onDefault);
    return () => window.removeEventListener(EVT, onDefault);
  }, []);

  return {
    theme,
    toggle: () =>
      setTheme((t) => {
        const next: Theme = t === "dark" ? "light" : "dark";
        try {
          localStorage.setItem(CHOICE, next);
        } catch {
          /* приватный режим */
        }
        return next;
      }),
  };
}
