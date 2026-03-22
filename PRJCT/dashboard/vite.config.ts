import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/bot": "http://localhost:8000",
      "/market": "http://localhost:8000",
      "/sentiment": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/news": "http://localhost:8000",
      "/intel": "http://localhost:8000",
    },
  },
});
