import { defineConfig, devices } from "@playwright/test";
export default defineConfig({ testDir: "./e2e", use: { baseURL: "http://localhost:3000", trace: "retain-on-failure" }, projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }, { name: "mobile", use: { ...devices["iPhone 13"] } }] });
