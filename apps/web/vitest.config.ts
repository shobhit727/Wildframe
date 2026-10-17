import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    // The suite runs many files concurrently; on a loaded machine a few
    // render-heavy tests exceed vitest's 5s default even though they are
    // not slow in isolation (Row.test.tsx runs in ~2s on its own). This
    // is contention, not a regression, so raise the ceiling rather than
    // splitting files or weakening the assertions.
    // Reset module mocks and restore spies between tests. Without this a
    // file that mutates a shared module (the api client, the auth store)
    // leaks it into files that run later, which showed up as
    // my-list's empty-state tests passing alone and failing in the suite.
    restoreMocks: true,
    clearMocks: true,
    mockReset: true,
    testTimeout: 20000,
    hookTimeout: 20000,

    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Without an explicit `include`, Vitest reports only files that were
    // actually imported by a test — untested files are invisible, so coverage
    // silently reads as 100% of whatever happens to be loaded. Setting
    // `include` is what pulls in the rest of src/ (Vitest 4 removed `all`).
    coverage: {
      provider: "v8",
      reportsDirectory: "./coverage",
      reporter: ["text", "html", "lcov", "json-summary"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/__tests__/**",
        "src/**/*.test.{ts,tsx}",
        "src/**/*.spec.{ts,tsx}",
        // Zero-byte Python-style artifacts; they compile to empty modules.
        "src/**/__init__.ts",
        // Type-only declarations are erased at runtime and emit no statements.
        "src/types/**",
        "**/*.config.{ts,tsx,mjs}",
        "next-env.d.ts",
        "e2e/**",
      ],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
