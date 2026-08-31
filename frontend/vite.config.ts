import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// В Docker фронт ходит на бэкенд по имени сервиса (http://backend:8000),
// локально без Docker — на http://localhost:8000. Управляется переменной VITE_API_PROXY.
const apiTarget = process.env.VITE_API_PROXY || "http://localhost:8000";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      // Новый SW применяется сам при следующем заходе — как деплой index.html сейчас.
      registerType: "autoUpdate",
      // Манифест НЕ генерируем: его отдаёт бэкенд из настроек заведения
      // (см. backend/core/branding.py). Ссылка на него — в index.html.
      // Иначе у всех кафе приложение установилось бы под одним именем.
      manifest: false,
      workbox: {
        // Оболочка приложения (хешированные js/css/html + иконки/шрифты) — в precache.
        globPatterns: ["**/*.{js,css,html,svg,png,ico,woff,woff2}"],
        // SPA-роутинг офлайн: любые переходы отдаём из index.html…
        navigateFallback: "/index.html",
        // …кроме бэкенда и файлов — их SW не перехватывает.
        navigateFallbackDenylist: [
          /^\/api/,
          /^\/admin/,
          /^\/media/,
          /^\/static/,
          // манифест и иконки заведения приходят с бэкенда, а не из оболочки
          /^\/manifest\.webmanifest$/,
          /^\/app-icon-/,
        ],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: true,
        runtimeCaching: [
          {
            // Живые данные (заказы, меню, статусы) — ТОЛЬКО из сети, без кэша.
            // Поведение 1-в-1 как сейчас: онлайн — свежие данные, офлайн — ошибка запроса.
            urlPattern: ({ url }) => url.pathname.startsWith("/api/"),
            handler: "NetworkOnly",
          },
          {
            // Фото блюд и логотип — из кэша мгновенно, обновление в фоне.
            urlPattern: ({ url }) => url.pathname.startsWith("/media/"),
            handler: "StaleWhileRevalidate",
            options: {
              cacheName: "media-images",
              expiration: { maxEntries: 120, maxAgeSeconds: 60 * 60 * 24 * 30 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // Google Fonts (стили) — свежие в фоне.
            urlPattern: ({ url }) => url.origin === "https://fonts.googleapis.com",
            handler: "StaleWhileRevalidate",
            options: { cacheName: "google-fonts-stylesheets" },
          },
          {
            // Google Fonts (сами файлы шрифтов) — надолго в кэш.
            urlPattern: ({ url }) => url.origin === "https://fonts.gstatic.com",
            handler: "CacheFirst",
            options: {
              cacheName: "google-fonts-webfonts",
              expiration: { maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 365 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
      // В dev SW не включаем — локальная разработка идёт как раньше (HMR без кэша).
      devOptions: { enabled: false },
    }),
  ],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": apiTarget,
      "/media": apiTarget,
    },
  },
});
