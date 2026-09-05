import { defineConfig, devices } from "@playwright/test";
export default defineConfig({ testDir: "./e2e", workers: 1, use: { baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000", trace: "retain-on-failure" }, projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }, { name: "mobile", use: { ...devices["iPhone 13"] } }] });
