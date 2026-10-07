import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:8000",
      "/events": "http://localhost:8000",
      "/coordinator": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});