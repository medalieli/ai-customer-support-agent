export type Identity = { kind: "customer" | "staff"; organization_id: string; subject_id: string; role: "support" | "admin" | null };
export type Conversation = { id: string; status: string; owner: string; ownership_state: string; title: string | null; locale: "en" | "fr"; created_at: string };
export type Message = { id: string; sequence_number: number; role: "customer" | "assistant" | "staff" | "system_event"; content: string; locale: string; created_at: string };
export type Citation = { title?: string; document_title?: string; section?: string | null; page?: number | null; snippet?: string; valid?: boolean; validation_status?: string };
export type Confirmation = { action_id: string; confirmation_token: string; expires_at: string | null; order_number?: string; masked_current_address?: unknown; proposed_address?: unknown; amount?: unknown; item?: unknown; fields_to_store?: unknown; purpose?: string; consequences?: string[]; citations?: Citation[] };
export type AgentRun = { run_id: string; status: string; duplicate: boolean; checkpoint_version: number; confirmation?: Confirmation | null; result?: Record<string, unknown> | null; message?: string | null };
export type Ticket = { id: string; conversation_id: string; reason_code: string; priority: string; status: string; assigned_staff_id: string | null; summary: Record<string, unknown>; version: number; created_at: string };
export type AuditTimelineItem = { id: string; category: string; action: string; outcome: string; reason_code: string | null; actor_type: string; occurred_at: string; metadata: Record<string, unknown> };
export type AuditTimelinePage = { items: AuditTimelineItem[]; next_cursor: string | null };
export type ApiProblem = { code: string; message: string; status: number };

const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const writes = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function idempotencyKey(scope: string) {
  return `${scope}-${crypto.randomUUID()}`;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (writes.has(method)) headers.set("X-CSRF-Token", "novacart-browser-v1");
  const response = await fetch(`${base}/api/v1${path}`, { ...init, method, headers, credentials: "include", cache: "no-store" });
  if (!response.ok) {
    let body: { error?: { code?: string; message?: string }; detail?: string } = {};
    try { body = await response.json(); } catch { /* safe generic error */ }
    throw { code: body.error?.code ?? body.detail ?? "request_failed", message: body.error?.message ?? "The request could not be completed.", status: response.status } satisfies ApiProblem;
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

export async function replaySse(runId: string, after: number, signal?: AbortSignal) {
  const response = await fetch(`${base}/api/v1/agent/runs/${runId}/events?after=${after}`, { credentials: "include", headers: { Accept: "text/event-stream", "Last-Event-ID": String(after) }, signal, cache: "no-store" });
  if (!response.ok) throw { code: "stream_failed", message: "Live updates are temporarily unavailable.", status: response.status } satisfies ApiProblem;
  const text = await response.text();
  return text.split("\n\n").flatMap((block) => {
    const id = Number(block.match(/^id: (\d+)/m)?.[1]);
    const type = block.match(/^event: (.+)$/m)?.[1];
    const data = block.match(/^data: (.+)$/m)?.[1];
    if (!id || !type || !data) return [];
    return [{ id, type, data: JSON.parse(data) as Record<string, unknown> }];
  });
}
