import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true
  },
  server: {
    proxy: {
      "/api": "http://localhost:8888"
    }
  }
});
