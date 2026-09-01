import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { get } from "./api";
import { applySiteDefault } from "./theme";
import type { AppTheme, Site } from "./types";

// Ключи совпадают с анти-FOUC скриптом в index.html: он ставит тему
// заведения до первой отрисовки, а сюда попадает то же самое как стартовое
// состояние — до ответа /api/site/.
const THEME_KEY = "app_theme";
const ACCENT_KEY = "app_accent";

// Осветление hex к белому: тёмный акцент заведения в ночном режиме нечитаем,
// поэтому производную для тёмной темы считаем автоматически.
function lighten(hex: string, k: number): string {
  const n = parseInt(hex.slice(1), 16);
  if (Number.isNaN(n)) return hex;
  const mix = (c: number) => Math.round(c + (255 - c) * k);
  const r = mix((n >> 16) & 255);
  const g = mix((n >> 8) & 255);
  const b = mix(n & 255);
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

/** Применить тему и акцент заведения к документу и запомнить для анти-FOUC. */
export function applyAppearance(theme: AppTheme, accent: string) {
  const el = document.documentElement;
  el.setAttribute("data-app-theme", theme);
  if (accent) {
    el.style.setProperty("--brand-user", accent);
    el.style.setProperty("--brand-user-dark", lighten(accent, 0.45));
  } else {
    el.style.removeProperty("--brand-user");
    el.style.removeProperty("--brand-user-dark");
  }
  try {
    localStorage.setItem(THEME_KEY, theme);
    localStorage.setItem(ACCENT_KEY, accent);
  } catch {
    /* приватный режим — просто без кэша */
  }
}

/** Название заведения — в заголовок вкладки и в подпись иконки на iOS.
 *  В index.html эти значения нейтральные: конкретное имя знает только API. */
function applyIdentity(site: Site) {
  const name = (site.name || "").trim();
  if (!name) return;
  document.title = name;
  const apple = document.querySelector('meta[name="apple-mobile-web-app-title"]');
  if (apple) apple.setAttribute("content", name);
}

type Appearance = {
  theme: AppTheme;
  accent: string;
  set: (theme: AppTheme, accent: string) => void;
};

const SiteContext = createContext<Site | null>(null);
const AppearanceContext = createContext<Appearance>({
  theme: "neutral",
  accent: "",
  set: () => {},
});

export function SiteProvider({ children }: { children: ReactNode }) {
  const [site, setSite] = useState<Site | null>(null);
  const [look, setLook] = useState<{ theme: AppTheme; accent: string }>(() => {
    try {
      return {
        theme: (localStorage.getItem(THEME_KEY) as AppTheme) || "neutral",
        accent: localStorage.getItem(ACCENT_KEY) || "",
      };
    } catch {
      return { theme: "neutral", accent: "" };
    }
  });

  const set = useCallback((theme: AppTheme, accent: string) => {
    applyAppearance(theme, accent);
    setLook({ theme, accent });
  }, []);

  useEffect(() => {
    get<Site>("/site/")
      .then((s) => {
        setSite(s);
        set(s.theme, s.accent_color);
        applyIdentity(s);
        applySiteDefault(s.dark_by_default);
      })
      .catch(() => {});
  }, [set]);

  return (
    <SiteContext.Provider value={site}>
      <AppearanceContext.Provider value={{ theme: look.theme, accent: look.accent, set }}>
        {children}
      </AppearanceContext.Provider>
    </SiteContext.Provider>
  );
}

export function useSite() {
  return useContext(SiteContext);
}

/** Текущая тема оформления заведения (не путать с день/ночь из theme.ts). */
export function useAppearance() {
  return useContext(AppearanceContext);
}
