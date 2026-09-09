import { expect, test } from "@playwright/test";

const apiBase = process.env.PLAYWRIGHT_API_URL ?? (process.env.E2E_RUN_ID ? "http://localhost:18000" : "http://localhost:8000");

test("delete conversation and resolved ticket with confirmation and access checks", async ({ page, browser }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "one live deletion workflow is sufficient");
  test.setTimeout(120_000);
  await login(page, "novacart|amira-en");
  await startConversation(page);
  const response = page.waitForResponse((item) => item.request().method() === "POST" && item.url().includes("/agent/threads/") && item.url().endsWith("/messages"));
  await ask(page, "I would like to speak to a human agent.");
  const sent = await response;
  const conversationId = sent.url().split("/threads/")[1].split("/")[0];
  const staffContext = await browser.newContext();
  const staff = await staffContext.newPage();
  try {
    await staff.goto("/");
    await staff.getByRole("tab", { name: "Staff" }).click();
    await staff.getByLabel("Password").fill("synthetic-demo-password");
    await staff.getByRole("button", { name: "Continue securely" }).click();
    await expect(staff).toHaveURL(/\/staff$/);
    const queueResponse = await staff.request.get(`${apiBase}/api/v1/staff/tickets`);
    const tickets = await queueResponse.json();
    const ticket = tickets.find((item: { conversation_id: string }) => item.conversation_id === conversationId);
    expect(ticket).toBeTruthy();
    expect((await page.request.delete(`${apiBase}/api/v1/staff/tickets/${ticket.id}?version=${ticket.version}`)).status()).toBe(403);
    expect((await staff.request.delete(`${apiBase}/api/v1/staff/tickets/${ticket.id}?version=${ticket.version}`)).status()).toBe(409);
    await staff.getByRole("navigation", { name: "Tickets grouped by customer" }).getByRole("button").filter({ hasText: ticket.id.slice(0, 8) }).click();
    await staff.getByRole("button", { name: "Claim", exact: true }).click();
    await expect(staff.getByRole("button", { name: "Close", exact: true })).toBeEnabled();
    await staff.getByRole("button", { name: "Close", exact: true }).click();
    await staff.getByLabel("Queue status").selectOption("closed");
    await staff.getByRole("navigation", { name: "Tickets grouped by customer" }).getByRole("button").filter({ hasText: ticket.id.slice(0, 8) }).click();
    staff.once("dialog", (dialog) => dialog.accept());
    await staff.getByRole("button", { name: "Delete ticket", exact: true }).click();
    await expect(staff.getByRole("navigation", { name: "Tickets grouped by customer" })).not.toContainText(ticket.id.slice(0, 8));
    expect((await staff.request.get(`${apiBase}/api/v1/staff/tickets/${ticket.id}`)).status()).toBe(404);
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.getByRole("button", { name: "Delete conversation" }).click();
    expect((await page.request.get(`${apiBase}/api/v1/conversations/${conversationId}`)).ok()).toBe(true);
    page.once("dialog", (dialog) => dialog.accept());
    const deleted = page.waitForResponse((item) => item.request().method() === "DELETE");
    await page.getByRole("button", { name: "Delete conversation" }).click();
    expect((await deleted).status()).toBe(204);
    expect((await page.request.get(`${apiBase}/api/v1/conversations/${conversationId}`)).status()).toBe(404);
    const list = await (await page.request.get(`${apiBase}/api/v1/conversations`)).json();
    expect(list.some((item: { id: string }) => item.id === conversationId)).toBe(false);
  } finally { await staffContext.close(); }
});

test("customer message appears before a direct human handoff reply", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "one live handoff is sufficient");
  test.setTimeout(120_000);
  await login(page, "novacart|amira-en");
  await startConversation(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/agent/threads/*/messages", async (route) => { await gate; await route.continue(); });
  const question = "I would like to speak to a human agent.";
  await page.getByLabel("Message NovaCart support").fill(question);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  try {
    await expect(page.locator(".message.customer")).toHaveText(new RegExp(question.replaceAll(".", "\\.")));
    await expect(page.locator(".typing-indicator")).toContainText("Nova is preparing a reply");
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
  } finally { release(); }
  await expect(page.locator(".message.assistant").last()).toContainText("A human support ticket has been opened and your message is saved.", { timeout: 90_000 });
  await expect(page.locator(".message.assistant").last()).not.toContainText("could not verify");
  await expect(page.locator(".message.customer")).toHaveCount(1);
  await expect(page.locator(".typing-indicator")).toHaveCount(0);
});

async function login(page: import("@playwright/test").Page, persona: string) {
  await page.goto("/");
  await page.getByLabel("Demo customer").selectOption(persona);
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page).toHaveURL(/\/chat$/);
}

async function startConversation(page: import("@playwright/test").Page) {
  const created = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/conversations"));
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  expect((await created).ok()).toBe(true);
  await expect(page.locator("li.message")).toHaveCount(0);
  await expect(page.getByLabel("Message NovaCart support")).toBeEnabled();
}

async function ask(page: import("@playwright/test").Page, message: string) {
  const priorAnswers = await page.locator("li.message.assistant").count();
  const submitted = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/messages"));
  await page.getByLabel("Message NovaCart support").fill(message);
  await page.getByRole("button", { name: "Send" }).click();
  expect((await submitted).ok()).toBe(true);
  await expect(page.locator("li.message.assistant")).toHaveCount(priorAnswers + 1, { timeout: 90_000 });
  await expect(page.getByRole("status")).toContainText("Connected securely", { timeout: 90_000 });
  return page.locator("li.message.assistant").last();
}

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
  await page.locator(".citations summary").last().click();
  await expect(page.locator(".citations .valid").last()).toBeVisible();
});

test("real OpenAI greeting stays in AI mode and missing order asks a question", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  test.skip(testInfo.project.name !== "desktop", "credential-gated model coverage runs once");
  await login(page, "novacart|amira-en");
  await startConversation(page);
  await expect(await ask(page, "hi")).toContainText(/Hi|Hello/i);
  await expect(await ask(page, "where is my order")).toContainText(/provide.*order number/i);
  await expect(page.getByText("handoff pending", { exact: true })).toHaveCount(0);
});

test("real OpenAI covers every seeded customer and order state", async ({ page }, testInfo) => {
  test.setTimeout(360_000);
  test.skip(testInfo.project.name !== "desktop", "credential-gated model coverage runs once");

  const personas = [
    {
      id: "novacart|amira-en",
      orders: [
        ["NC-1001", /unfulfilled|processing|not yet shipped/i],
        ["NC-1002", /shipped|transit|NovaPost/i],
        ["NC-1003", /delay|weather/i],
        ["NC-1004", /delivered/i],
        ["NC-1005", /delivered/i],
      ] as const,
    },
    {
      id: "novacart|lucas-fr",
      orders: [
        ["NC-1006", /livr|delivered/i],
        ["NC-1007", /parti|partial/i],
        ["NC-1008", /annul|cancel/i],
        ["NC-1009", /rembours|refund/i],
      ] as const,
    },
    { id: "orbit-outlet|nora-en", orders: [["NC-1001", /unfulfilled|processing|not yet shipped/i]] as const },
  ];

  for (const persona of personas) {
    await login(page, persona.id);
    for (const [order, expectedState] of persona.orders) {
      await startConversation(page);
      const answer = await ask(page, `What is the current status of my order ${order}?`);
      await expect(answer).toContainText(order);
      await expect(answer).toContainText(expectedState);
    }
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/$/);
  }
});

test("real OpenAI confirmed address write waits for mock commerce", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  test.skip(testInfo.project.name !== "desktop", "real-provider smoke runs once; responsive workflows run deterministically on both viewports");
  await page.goto("/");
  await page.getByLabel("Demo customer").selectOption("novacart|amira-en");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await page.getByLabel("Message NovaCart support").fill("Change shipping address NC-1001 immediately; recipient: Synthetic Test; line1: 20 Test Street; city: Boston; region: MA; postal code: 02113; country code: US");
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
    expect((await order.json()).shipping_address.line1).toBe("20 Test Street");
  }
});

test("staff tickets are grouped by customer and summaries are readable", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "staff layout smoke runs once");
  await page.goto("/");
  await page.getByRole("tab", { name: "Staff" }).click();
  await page.getByLabel("Password").fill("synthetic-demo-password");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(page).toHaveURL(/\/staff$/);
  const queue = page.getByRole("navigation", { name: "Tickets grouped by customer" });
  await expect(queue.getByText("Amira Haddad", { exact: true })).toBeVisible();
  await queue.locator(".ticket-row").first().click();
  await expect(page.getByRole("heading", { name: "Customer and ticket details" })).toBeVisible();
  await expect(page.locator(".summary-card pre")).toHaveCount(0);
  await expect(page.locator(".customer-details")).toContainText("amira@synthetic.test");
});
