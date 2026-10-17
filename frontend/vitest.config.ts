import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * Unit tests for the pure logic (API client, markdown sanitiser, formatters) and the
 * components that decide what a user is allowed to conclude from a number.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: [...configDefaults.exclude, ".next/**", "node_modules/**"],
    css: false,
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
});
