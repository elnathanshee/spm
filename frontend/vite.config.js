import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

const backendProxy = {
  target: "http://127.0.0.1:8000",
  configure(proxy) {
    proxy.on("error", (_error, _request, response) => {
      if (response.headersSent) return;
      response.writeHead(503, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        detail: "Cannot reach the backend service. Start FastAPI on port 8000 and try again.",
      }));
    });
  },
};

export default defineConfig({
  plugins: [tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/auth": backendProxy,
      "/events": backendProxy,
      "/venues": backendProxy,
      "/coordinator": backendProxy,
      "/lead": backendProxy,
      "/notifications": backendProxy,
      "/equipment-requests": backendProxy,
      "/equipment": backendProxy,
      "/safety": backendProxy,
      "/registrations": backendProxy,
    },
  },
  build: {
    outDir: "dist",
  },
});
