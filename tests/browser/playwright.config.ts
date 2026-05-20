import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 60_000,
  workers: 1,
  use: {
    baseURL: process.env.BASE_URL ?? "http://127.0.0.1:18080",
    headless: true,
  },
});
