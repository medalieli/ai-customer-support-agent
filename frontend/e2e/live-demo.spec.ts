import { expect, test } from "@playwright/test";
test("real OpenAI FAQ turn persists in customer chat", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  test.skip(testInfo.project.name !== "desktop", "one real-browser demonstration is sufficient");
  await page.goto("/");
  await page.getByLabel("Demo customer").selectOption("novacart|amira-en");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await page.getByLabel("Message NovaCart support").fill("What is NovaCart's return policy?");
  await page.getByRole("button", { name: "Send" }).click();
  const answer = page.locator("li.message.assistant").last();
  await expect(answer).toBeVisible({ timeout: 60_000 });
  await expect(answer.locator(":scope > p").first()).not.toBeEmpty();
  await expect(page.getByRole("status")).toContainText("Connected securely", { timeout: 60_000 });
  await expect(page.locator(".citations .valid").last()).toBeVisible();
});

test("real OpenAI confirmed address write waits for mock commerce", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  test.skip(testInfo.project.name !== "desktop", "real-provider smoke runs once; responsive workflows run deterministically on both viewports");
  await page.goto("/");
  await page.getByLabel("Demo customer").selectOption("novacart|amira-en");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await page.getByLabel("Message NovaCart support").fill("Change shipping address NC-1001 immediately; recipient: Synthetic Test; line1: 20 OpenAI Street; city: Boston; region: MA; postal code: 02113; country code: US");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeVisible({ timeout: 90_000 });
  const confirmation = page.waitForResponse((response) => response.url().endsWith("/resume") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Approve" }).click();
  const response = await confirmation;
  expect(response.ok()).toBe(true);
  expect((await response.json()).status).not.toBe("failed");
  expect(response.request().headers()["idempotency-key"]).toBeTruthy();
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeHidden();
  await expect(page.getByRole("status")).toContainText("Connected securely");
  if (process.env.E2E_RUN_ID) {
    const order = await page.request.get("http://localhost:18080/v1/orders/ord-address", { headers: {
      "X-Internal-API-Key": "development-commerce-key",
      "X-Organization-Id": "10000000-0000-0000-0000-000000000001",
      "X-External-Customer-Id": "seed-amira-en",
    } });
    expect(order.ok()).toBe(true);
    expect((await order.json()).shipping_address.line1).toBe("20 OpenAI Street");
  }
});
