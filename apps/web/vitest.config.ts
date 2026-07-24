import path from "node:path";
import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [tsconfigPaths({ projects: [path.resolve(__dirname, "tsconfig.json")] })],
  test: {
    environment: "node",
    include: ["core/**/*.test.ts", "core/**/*.spec.ts", "ce/**/*.test.ts", "ce/**/*.spec.ts"],
  },
});
