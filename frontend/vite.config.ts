import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Fail rather than silently moving to 5174: the API's CORS allow-list names
    // 5173 explicitly, so a shifted port would look like a mysterious CORS bug.
    strictPort: true,
  },
});
