import { expect, test } from "@playwright/test";
test("real OpenAI FAQ turn persists in customer chat", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  test.skip(testInfo.project.name !== "desktop", "one real-browser demonstration is sufficient");
  await page.goto("/");
  await page.getByLabel("Demo customer").selectOption("amira-en");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await page.getByLabel("Message NovaCart support").fill("What is NovaCart's return policy?");
  await page.getByRole("button", { name: "Send" }).click();
  const answer = page.locator("li.message.assistant").last();
  await expect(answer).toBeVisible({ timeout: 60_000 });
  await expect(answer.locator(":scope > p").first()).not.toBeEmpty();
  await expect(page.getByRole("status")).toContainText("Connected securely", { timeout: 60_000 });
});
