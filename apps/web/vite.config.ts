import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (
            id.includes("/react/") ||
            id.includes("react-dom") ||
            id.includes("scheduler") ||
            id.includes("@tanstack/react-virtual")
          ) {
            return "react-vendor";
          }
          if (
            id.includes("react-markdown") ||
            id.includes("remark-") ||
            id.includes("rehype-") ||
            id.includes("katex") ||
            id.includes("highlight.js") ||
            id.includes("micromark") ||
            id.includes("mdast") ||
            id.includes("hast") ||
            id.includes("unified") ||
            id.includes("unist") ||
            id.includes("parse5") ||
            id.includes("vfile") ||
            id.includes("property-information") ||
            id.includes("space-separated-tokens") ||
            id.includes("comma-separated-tokens")
          ) {
            return "markdown";
          }
          if (id.includes("lightweight-charts")) return "charts";
          return undefined;
        },
      },
    },
  },
});
