import { defineConfig } from "vite";

// Dev server proxies /api to the FastAPI backend so the frontend never
// needs to know the backend origin (and SSE works same-origin, no CORS).
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
