import { expect, test } from "@playwright/test";
test("customer login is responsive and accessible", async ({ page }) => { await page.goto("/"); await expect(page.getByRole("heading", { name: "Answers that understand your order." })).toBeVisible(); await expect(page.getByRole("tab", { name: "Customer" })).toBeVisible(); await expect(page.getByText(/HttpOnly cookie/)).toBeVisible(); });
test("staff login exposes no customer token storage", async ({ page }) => { await page.goto("/"); await page.getByRole("tab", { name: "Staff" }).click(); await expect(page.getByLabel("Email")).toHaveValue("support@novacart.test"); expect(await page.evaluate(() => localStorage.length)).toBe(0); });
test("staff analytics loads bounded operational aggregates", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Staff" }).click();
  await page.getByLabel("Password").fill("synthetic-demo-password");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await page.getByRole("button", { name: /Analytics/ }).click();
  await expect(page.getByRole("heading", { name: "Service overview" })).toBeVisible();
  await expect(page.getByText("Conversations", { exact: true })).toBeVisible();
  await page.getByLabel("Time range").selectOption("24h");
  await expect(page.getByRole("heading", { name: "Provider health" })).toBeVisible();
});
