import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/") && /(@tiptap|prosemirror|orderedmap|rope-sequence)/.test(id)) return "editor";
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/testSetup.js"]
  },
  server: {
    proxy: {
      "/api": "http://localhost:8888"
    }
  }
});
