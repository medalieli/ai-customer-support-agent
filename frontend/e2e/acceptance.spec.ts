import AxeBuilder from "@axe-core/playwright";
import { expect, Page, test } from "@playwright/test";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const apiBase = (process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:13000").replace(":13000", ":18000");
const runId = process.env.E2E_RUN_ID ?? crypto.randomUUID();
const project = process.env.E2E_PROJECT ?? (process.env.E2E_RUN_ID ? `novacart-e2e-${runId}` : "");
test.beforeEach(() => {
  expect(project, "Use scripts/run-e2e.ps1; never run acceptance against demo data").toMatch(/^novacart-e2e-[a-f0-9]{10}$/);
});

async function decide(page: Page, name: "Approve" | "Cancel") {
  const responsePromise = page.waitForResponse((response) => response.url().endsWith("/resume") && response.request().method() === "POST");
  await page.getByRole("button", { name, exact: true }).click();
  const response = await responsePromise;
  expect(response.request().headers()["idempotency-key"]).toBeTruthy();
  expect(response.ok()).toBe(true);
  const result = await response.json();
  const successfulApprovals = ["action_completed", "refund_request_submitted"];
  expect(name === "Approve" ? successfulApprovals : ["action_cancelled"]).toContain(result.status);
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeHidden();
  await expect(page.getByRole("status")).toContainText("Connected securely");
  return result;
}

async function customerLogin(page: Page, name = "Amira Haddad") {
  await page.goto("/", { waitUntil: "networkidle" });
  const value = name === "Nora Silva" ? "orbit-outlet|nora-en" : name === "Lucas Martin" ? "novacart|lucas-fr" : "novacart|amira-en";
  await page.getByLabel("Demo customer").selectOption(value);
  const hydrated = page.waitForResponse((response) => response.url().endsWith("/conversations") && response.request().method() === "GET");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  expect((await hydrated).ok()).toBe(true);
}

async function signOut(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/$/);
}

async function newConversation(page: Page) {
  const created = page.waitForResponse((response) => response.url().endsWith("/conversations") && response.request().method() === "POST");
  const button = page.getByRole("button", { name: "New conversation", exact: true });
  await button.click();
  const response = await created;
  expect(response.ok()).toBe(true);
  await expect(page.getByLabel("Message NovaCart support")).toBeEnabled();
  return (await response.json()).id as string;
}

async function send(page: Page, message: string) {
  const response = page.waitForResponse((item) => item.request().method() === "POST" && item.url().includes("/agent/threads/") && item.url().endsWith("/messages"));
  await page.getByLabel("Message NovaCart support").fill(message);
  await page.getByRole("button", { name: "Send" }).click();
  expect((await response).ok()).toBe(true);
  await expect(page.getByRole("status")).toContainText("Connected securely", { timeout: 30_000 });
}

test("greetings and general questions stay in AI mode", async ({ page }) => {
  await customerLogin(page);
  const conversation = await newConversation(page);
  await send(page, "hi");
  await expect(page.locator(".message.assistant").last()).toContainText(/Hi|Hello/i);
  await send(page, "What is the capital of Japan?");
  await expect(page.locator(".message.assistant").last()).toContainText(/orders|policies/i);
  const state = await page.request.get(`${apiBase}/api/v1/conversations/${conversation}`);
  expect((await state.json()).ownership_state).toBe("ai_active");
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
});

test("English and French FAQ answers have validated citations", async ({ page }) => {
  await customerLogin(page);
  await newConversation(page);
  await send(page, "What is the NovaCart return policy?");
  await page.locator(".citations summary").last().click();
  await expect(page.locator(".citations .valid").last()).toContainText(/valid/i);
  await signOut(page);
  await customerLogin(page, "Lucas Martin");
  await newConversation(page);
  await send(page, "Puis-je retourner un article admissible dans les 30 jours civils suivant la livraison ?");
  await page.locator(".citations summary").last().click();
  await expect(page.locator(".citations .valid").last()).toContainText(/valid/i);
});

test("order tracking and address approval/cancellation use confirmed backend state", async ({ page }) => {
  await customerLogin(page);
  await newConversation(page);
  await send(page, "Track my order NC-1001");
  await expect(page.locator(".message.assistant").last()).toContainText(/NC-1001|unfulfilled|processing/i);
  await send(page, "Change shipping address NC-1001 immediately; recipient: Synthetic Test; line1: 10 Test Street; city: Boston; region: MA; postal code: 02113; country code: US");
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeVisible();
  await decide(page, "Cancel");
  await send(page, "Change shipping address NC-1001 immediately; recipient: Synthetic Test; line1: 11 Test Street; city: Boston; region: MA; postal code: 02113; country code: US");
  await decide(page, "Approve");
});

test("refund eligibility and CRM consent require explicit decisions", async ({ page }) => {
  await customerLogin(page);
  await newConversation(page);
  await send(page, "Refund order NC-1004; reason: changed mind; SKU: MSE-PRO; quantity: 1; amount: USD 69.00");
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeVisible();
  await decide(page, "Approve");
  await newConversation(page);
  await send(page, "I want an enterprise product demo and to speak with sales by email");
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeVisible();
  await decide(page, "Cancel");
  await newConversation(page);
  await send(page, "I want an enterprise product demo and to speak with sales by email");
  await decide(page, "Approve");
  await signOut(page);
  await customerLogin(page, "Lucas Martin");
  await newConversation(page);
  await send(page, "Refund order NC-1006; reason: changed mind; SKU: CASE-RED; quantity: 1; amount: USD 19.00");
  await expect(page.locator(".message.assistant").last()).toContainText(/ineligible|admissible/i);
});

test("SSE reconnect replays without duplicate logical messages", async ({ page }) => {
  let disconnected = false;
  await page.route("**/agent/runs/*/events*", async (route) => {
    if (!disconnected) { disconnected = true; await route.abort("connectionfailed"); }
    else await route.continue();
  });
  await customerLogin(page, "Nora Silva");
  await newConversation(page);
  await send(page, "What is the warranty policy?");
  await expect(page.locator(".message.customer", { hasText: "What is the warranty policy?" })).toHaveCount(1);
  await expect(page.locator(".message.assistant").last()).toBeVisible();
  await expect(page.locator(".message.assistant")).toHaveCount(1);
  expect(disconnected).toBe(true);
});

test("expired confirmation disables writes", async ({ page }) => {
  await customerLogin(page);
  await newConversation(page);
  await page.clock.install({ time: new Date("2099-01-01T00:00:00Z") });
  await send(page, "Change shipping address NC-1001 immediately; recipient: Synthetic Test; line1: 12 Test Street; city: Boston; region: MA; postal code: 02113; country code: US");
  const approval = page.getByRole("button", { name: "Approve" });
  await expect(approval).toBeVisible();
  await expect(approval).toBeDisabled();
});

test("server rejects an expired confirmation even when the browser still enables it", async ({ page }) => {
  await customerLogin(page);
  await newConversation(page);
  const pending = page.waitForResponse((response) => response.url().includes("/agent/threads/") && response.url().endsWith("/messages") && response.request().method() === "POST");
  await send(page, "Change shipping address NC-1001 immediately; recipient: Synthetic Test; line1: 15 Test Street; city: Boston; region: MA; postal code: 02113; country code: US");
  const actionId = (await (await pending).json()).confirmation.action_id;
  expect(actionId).toMatch(/^[a-f0-9-]{36}$/);
  await promisify(execFile)("docker", ["exec", `${project}-postgres-1`, "psql", "-U", "novacart", "-d", "novacart", "-c",
    `UPDATE pending_actions SET expires_at = now() - interval '1 second' WHERE id = '${actionId}' AND organization_id = '10000000-0000-0000-0000-000000000001'`]);
  const resumed = page.waitForResponse((response) => response.url().endsWith("/resume"));
  await page.getByRole("button", { name: "Approve" }).click();
  const response = await resumed;
  const result = await response.json();
  expect(result.status).toBe("action_cancelled");
  expect(result.result.reason_code).toBe("expired");
  await expect(page.getByRole("status")).toContainText(/expired/i);
});

test("customer FAQ can be completed using only the keyboard", async ({ page }) => {
  async function tabTo(selector: string) {
    for (let i = 0; i < 35; i++) {
      await page.keyboard.press("Tab");
      if (await page.locator(selector).evaluateAll((nodes) => nodes.some((node) => node === document.activeElement))) {
        await expect(page.locator(":focus-visible")).toBeVisible();
        return;
      }
    }
    throw new Error(`Keyboard could not reach ${selector}`);
  }
  await page.goto("/");
  await expect(page.locator("select[name=persona] option")).toHaveCount(3);
  await tabTo("select[name=persona]");
  await page.keyboard.press("Home");
  await tabTo("button.primary");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/chat$/);
  await tabTo("aside button.primary");
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Message NovaCart support")).toBeEnabled();
  await tabTo("textarea[name=message]");
  await page.keyboard.type("What is the warranty policy?");
  await tabTo(".composer button");
  await page.keyboard.press("Enter");
  await page.locator(".citations summary").last().click();
  await expect(page.locator(".citations .valid").last()).toBeVisible();
});

test("stale confirmation conflict refreshes server state", async ({ page }) => {
  let run: { conversation_id: string; checkpoint_version: number; confirmation: { action_id: string; confirmation_token: string } } | undefined;
  page.on("response", async (response) => {
    if (response.request().method() === "POST" && response.url().includes("/agent/threads/") && response.url().endsWith("/messages")) run = { ...(await response.json()), conversation_id: response.url().match(/threads\/([^/]+)/)![1] };
  });
  await customerLogin(page);
  await newConversation(page);
  await send(page, "Change shipping address NC-1001 immediately; recipient: Synthetic Test; line1: 13 Test Street; city: Boston; region: MA; postal code: 02113; country code: US");
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
  expect(run?.confirmation).toBeTruthy();
  await page.evaluate(async (captured) => {
    await fetch(`http://localhost:18000/api/v1/agent/threads/${captured.conversation}/resume`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRF-Token": "novacart-browser-v1", "Idempotency-Key": `competing-${crypto.randomUUID()}` }, body: JSON.stringify({ checkpoint_version: captured.version, action_id: captured.action, confirmation_token: captured.token, decision: "deny", value: { decision: "deny" } }) });
  }, { conversation: run!.conversation_id, version: run!.checkpoint_version, action: run!.confirmation.action_id, token: run!.confirmation.confirmation_token });
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("status")).toContainText(/stale|conflicted/i);
});

test("confirmation retries reuse the key after a lost server response", async ({ page }) => {
  await customerLogin(page);
  await newConversation(page);
  await send(page, "Change shipping address NC-1001 immediately; recipient: Synthetic Test; line1: 16 Test Street; city: Boston; region: MA; postal code: 02113; country code: US");
  const keys: string[] = [];
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/agent/threads/*/resume", async (route) => {
    keys.push(route.request().headers()["idempotency-key"]);
    if (keys.length === 1) {
      await gate;
      const realResponse = await route.fetch();
      expect(realResponse.ok()).toBe(true);
      await route.abort("connectionfailed");
    } else await route.continue();
  });
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("button", { name: "Confirming" })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "Review before continuing" })).toBeVisible();
  release();
  await expect(page.getByRole("status")).toContainText(/fetch|network|verified|load/i);
  await decide(page, "Approve");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});

test("provider not-found failure enters a safe handoff", async ({ page }) => {
  await customerLogin(page);
  await newConversation(page);
  await send(page, "Track my order NC-9999");
  await expect(page.locator(".ownership")).toContainText(/handoff|staff/);
  await expect(page.locator(".message.assistant").last()).toContainText(
    /could not find.*order.*your account/i,
  );
  await expect(page.locator("body")).not.toContainText(/traceback|secret|provider payload/i);
});

test("open staff and customer workspaces receive new handoffs and replies", async ({ browser }) => {
  const staffContext = await browser.newContext();
  const customerContext = await browser.newContext();
  const staff = await staffContext.newPage();
  const customer = await customerContext.newPage();
  try {
    await staff.goto("/");
    await staff.getByRole("tab", { name: "Staff" }).click();
    await staff.getByLabel("Password").fill("synthetic-demo-password");
    await staff.getByRole("button", { name: "Continue securely" }).click();
    await expect(staff).toHaveURL(/\/staff$/);

    await customerLogin(customer);
    const conversationId = await newConversation(customer);
    await send(customer, "I need a human representative");
    await expect(customer.locator(".message.assistant").last()).toContainText(
      /human support ticket/i,
    );

    const tickets = await (await staff.request.get(`${apiBase}/api/v1/staff/tickets`)).json();
    const ticket = tickets.find((item: { conversation_id: string }) => item.conversation_id === conversationId);
    expect(ticket).toBeTruthy();
    const ticketRow = staff.getByRole("navigation", { name: "Tickets grouped by customer" })
      .getByRole("button").filter({ hasText: ticket.id.slice(0, 8) });
    await expect(ticketRow).toBeVisible({ timeout: 10_000 });
    await ticketRow.click();
    await customer.getByLabel("Message NovaCart support").fill("A new customer detail");
    await customer.getByLabel("Message NovaCart support").press("Enter");
    await expect(staff.getByText("A new customer detail", { exact: true })).toBeVisible({ timeout: 10_000 });

    await staff.getByRole("button", { name: "Claim" }).click();
    await staff.getByLabel("Public reply").fill("A live staff reply");
    await staff.getByRole("button", { name: "Send to customer" }).click();
    await expect(customer.getByText("A live staff reply", { exact: true })).toBeVisible({ timeout: 10_000 });
  } finally {
    await staffContext.close();
    await customerContext.close();
  }
});

test("commerce outage produces a safe handoff without reporting a successful write", async ({ page }) => {
  test.setTimeout(90_000);
  const compose = ["compose", "-p", project, "-f", "../compose.yaml", "-f", "../compose.e2e.yaml"];
  const docker = promisify(execFile);
  await customerLogin(page);
  await newConversation(page);
  try {
    await docker("docker", [...compose, "stop", "mock-commerce"]);
    const failedRun = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/messages"));
    await send(page, "Track my order NC-1001");
    expect(["failed", "handoff_pending", "escalation_required"]).toContain((await (await failedRun).json()).status);
    await expect(page.getByRole("status")).toContainText(/connected|saved|error|unavailable/i);
    await expect(page.locator("body")).not.toContainText(/traceback|secret|successfully updated/i);
  } finally {
    await docker("docker", [...compose, "up", "--wait", "-d", "mock-commerce"]);
  }
});

test("human handoff supports staff lifecycle, audit, and private-note isolation", async ({ page }) => {
  await customerLogin(page);
  const conversationId = await newConversation(page);
  await send(page, "I need a human representative");
  await expect(page.locator(".ownership")).toContainText(/handoff|staff/);
  await signOut(page);
  await page.getByRole("tab", { name: "Staff" }).click();
  await page.getByLabel("Password").fill("synthetic-demo-password");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page).toHaveURL(/\/staff$/);
  const tickets = await (await page.request.get(`${apiBase}/api/v1/staff/tickets`)).json();
  const ticket = tickets.find((item: { conversation_id: string }) => item.conversation_id === conversationId);
  expect(ticket).toBeTruthy();
  const ticketRow = page.getByRole("navigation", { name: "Tickets grouped by customer" })
    .getByRole("button").filter({ hasText: ticket.id.slice(0, 8) });
  await ticketRow.focus();
  await ticketRow.press("Enter");
  await page.getByLabel("Public reply").fill(`Public E2E reply ${runId}`);
  await page.getByRole("button", { name: "Send to customer" }).click();
  await page.getByLabel("Private internal note").fill(`PRIVATE-E2E-NOTE ${runId}`);
  await page.getByRole("button", { name: "Add private note" }).click();
  await expect(page.getByRole("heading", { name: "Audit timeline" })).toBeVisible();
  await expect(page.locator(".audit-timeline")).not.toContainText("PRIVATE-E2E-NOTE");
  await page.getByRole("button", { name: "Resolve", exact: true }).click();
  await page.getByRole("button", { name: "Return to AI", exact: true }).click();
  await expect(page.locator(".audit-timeline")).toContainText(/return|resum/i);
  await page.context().clearCookies();
  await customerLogin(page);
  await expect(page.getByText(`Public E2E reply ${runId}`, { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("PRIVATE-E2E-NOTE");
  const history = await page.request.get(`${apiBase}/api/v1/conversations/${conversationId}/messages`);
  expect(history.ok()).toBe(true);
  expect(await history.text()).not.toContain("PRIVATE-E2E-NOTE");
  await send(page, "What is the warranty policy?");
  await page.locator(".citations summary").last().click();
  await expect(page.locator(".citations .valid").last()).toBeVisible();
  await page.context().clearCookies();
  await customerLogin(page, "Nora Silva");
  expect((await page.request.get(`${apiBase}/api/v1/conversations/${conversationId}/messages`)).status()).toBe(404);
  expect((await page.request.get(`${apiBase}/api/v1/staff/tickets`)).status()).toBe(403);
});

test("customer cannot enter staff route and tenants remain isolated", async ({ page }) => {
  await customerLogin(page, "Nora Silva");
  await expect(page.getByText("Public E2E reply")).toHaveCount(0);
  await page.goto("/staff");
  await expect(page).toHaveURL(/\/$/);
});

test("keyboard, focus, accessibility, and viewport overflow", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus-visible")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
  await customerLogin(page);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await newConversation(page);
  await send(page, "Change shipping address NC-1001 immediately; recipient: Synthetic Test; line1: 14 Test Street; city: Boston; region: MA; postal code: 02113; country code: US");
  await expect(page.getByRole("button", { name: "Approve" })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await decide(page, "Cancel");
  await page.screenshot({ path: testInfo.outputPath("customer.png"), fullPage: true });
  await signOut(page);
  await page.getByRole("tab", { name: "Staff" }).click();
  await page.getByLabel("Password").fill("synthetic-demo-password");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page).toHaveURL(/\/staff$/);
  const queue = page.getByRole("navigation", { name: "Tickets grouped by customer" });
  await expect(queue.getByText("Amira Haddad", { exact: true })).toBeVisible();
  const firstTicket = queue.locator(".ticket-row").first();
  await firstTicket.focus();
  await firstTicket.press("Enter");
  await expect(page.getByRole("heading", { name: "Audit timeline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Customer and ticket details" })).toBeVisible();
  await expect(page.locator(".summary-card pre")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("staff.png"), fullPage: true });
});
