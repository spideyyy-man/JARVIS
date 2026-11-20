import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = "http://127.0.0.1:8765";
const proxy = {
  "/api": { target: backend, changeOrigin: false },
  "/ws": { target: backend, ws: true, changeOrigin: false },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: "127.0.0.1", proxy },
  preview: { port: 5173, host: "127.0.0.1", proxy },
  // the three.js chunk (~545 kB) is only loaded when Settings > "3D core" is on, so it is allowed past the 500 kB warning
  build: { outDir: "dist", assetsDir: "assets", emptyOutDir: true, chunkSizeWarningLimit: 600 },
});
