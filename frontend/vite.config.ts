import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В Docker фронт ходит на бэкенд по имени сервиса (http://backend:8000),
// локально без Docker — на http://localhost:8000. Управляется переменной VITE_API_PROXY.
const apiTarget = process.env.VITE_API_PROXY || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": apiTarget,
      "/media": apiTarget,
    },
  },
});
