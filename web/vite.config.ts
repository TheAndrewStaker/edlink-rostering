import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Deterministic ports driven by the parent shell's EDLINK_PORT_BASE
// (see scripts/_lib.sh). scripts/web-dev.sh exports the
// derived ports before calling vite. Defaults match .env.example so
// running `npm run dev` directly still works in a fresh checkout.
const portWeb = Number(process.env.EDLINK_PORT_WEB ?? 8101);
const portApi = Number(process.env.EDLINK_PORT_API ?? 8100);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: portWeb,
    proxy: {
      "/api": `http://127.0.0.1:${portApi}`,
    },
  },
});
