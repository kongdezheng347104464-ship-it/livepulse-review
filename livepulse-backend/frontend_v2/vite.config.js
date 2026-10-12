import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// frontend_v2 — 跑在 3002，不跟旧 frontend (3001) 冲突
// proxy 同样指向 backend 8004，复用同一套 API
export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.VITE_PORT || 3002),
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
