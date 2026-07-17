import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/graphql": "http://localhost:8000",
      "/chat": "http://localhost:8000",
      "/agent": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
      "/sites": "http://localhost:8000",
      "/auth": "http://localhost:8000",
      "/bots": "http://localhost:8000",
      "/webhooks": "http://localhost:8000",
      "/onboard": "http://localhost:8000",
      "/connectors": "http://localhost:8000",
    },
  },
});
