import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: true } },
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: { target: "safari15", sourcemap: true },
});
