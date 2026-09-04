import { defineConfig } from "vitest/config";
export default defineConfig({
  resolve: { alias: { "@": import.meta.dirname } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    exclude: ["e2e/**", "node_modules/**", ".next/**"],
    coverage: {
      provider: "v8",
      reporter: ["text"],
      include: ["components/api-status.tsx"],
      thresholds: { lines: 90, functions: 90, statements: 90, branches: 75 },
    },
  },
});
