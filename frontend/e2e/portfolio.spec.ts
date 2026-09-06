import { expect, Page, test } from "@playwright/test";
import path from "node:path";

const gallery = path.resolve(process.cwd(), "../docs/portfolio");
const staffPassword = "synthetic-demo-password";

async function login(page: Page, persona = "novacart|amira-en") {
  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByLabel("Demo customer").selectOption(persona);
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page).toHaveURL(/\/chat$/);
}
async function fresh(page: Page) {
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await expect(page.getByLabel("Message NovaCart support")).toBeEnabled();
}
async function send(page: Page, text: string) {
  const response = page.waitForResponse((item) => item.request().method() === "POST" && item.url().includes("/agent/threads/") && item.url().endsWith("/messages"));
  await page.getByLabel("Message NovaCart support").fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  expect((await response).ok()).toBe(true);
  await expect(page.getByRole("status")).toContainText("Connected securely", { timeout: 30_000 });
}
async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(gallery, name), type: "jpeg", quality: 84, animations: "disabled" });
}

test("capture the fictional NovaCart portfolio journey @portfolio", async ({ page }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);

  await fresh(page);
  await send(page, "What is the NovaCart return policy?");
  await expect(page.locator(".citations .valid").last()).toBeVisible();
  await shot(page, "01-grounded-faq.jpg");

  await fresh(page);
  await send(page, "Track my order NC-1002");
  await expect(page.locator(".message.assistant").last()).toContainText(/NC-1002|transit|shipped/i);
  await shot(page, "02-live-order-tracking.jpg");

  await fresh(page);
  await send(page, "Change shipping address NC-1001 immediately; recipient: Amira Haddad; line1: 10 Demo Street; city: Boston; region: MA; postal code: 02113; country code: US");
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeVisible();
  await shot(page, "03-address-confirmation.jpg");
  await page.getByRole("button", { name: "Cancel" }).click();

  await fresh(page);
  await send(page, "Refund order NC-1004; reason: changed mind; SKU: MSE-PRO; quantity: 1; amount: USD 69.00");
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeVisible();
  await shot(page, "04-refund-confirmation.jpg");
  await page.getByRole("button", { name: "Cancel" }).click();

  await fresh(page);
  await send(page, "I want an enterprise product demo and consent to being contacted by sales by email");
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeVisible();
  await shot(page, "05-crm-consent.jpg");
  await page.getByRole("button", { name: "Cancel" }).click();

  await fresh(page);
  await send(page, "I need a human representative");
  await expect(page.locator(".ownership")).toContainText(/handoff|staff/);
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByRole("tab", { name: "Staff" }).click();
  await page.getByLabel("Password").fill(staffPassword);
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page).toHaveURL(/\/staff$/);
  const ticket = page.getByRole("button", { name: /explicit human request/i }).last();
  await ticket.click();
  await page.getByRole("button", { name: "Claim" }).click();
  await page.getByLabel("Public reply").fill("Hi Amira — I have your request and will help from here.");
  await page.getByRole("button", { name: "Send to customer" }).click();
  await page.locator(".ticket-detail").evaluate((element) => { element.scrollTop = 0; });
  await shot(page, "06-human-handoff-staff.jpg");

  await page.getByRole("button", { name: /Analytics/ }).click();
  await expect(page.getByRole("heading", { name: "Service overview" })).toBeVisible();
  await expect(page.getByText("Loading operational analytics…")).toBeHidden();
  await shot(page, "07-analytics-dashboard.jpg");

  await page.context().clearCookies();
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, "novacart|lucas-fr");
  await fresh(page);
  await send(page, "What is the warranty policy?");
  await expect(page.locator(".citations .valid").last()).toBeVisible();
  await shot(page, "08-mobile-customer-support.jpg");
});
