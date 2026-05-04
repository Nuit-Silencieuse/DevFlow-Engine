import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.DEVFLOW_CONTROL_PLANE_URL || "http://localhost:8080";

  return {
    server: {
      port: 5173,
      strictPort: false,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
        },
      },
    },
  };
});
