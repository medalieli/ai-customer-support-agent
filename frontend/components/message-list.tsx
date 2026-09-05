"use client";
import type { Citation, Message } from "@/lib/api";

function structured(content: string): Record<string, unknown> | null {
  try { const value = JSON.parse(content) as unknown; return value && typeof value === "object" ? value as Record<string, unknown> : null; } catch { return null; }
}

function Citations({ items }: { items: Citation[] }) {
  if (!items.length) return null;
  return <aside className="citations" aria-label="Sources"><h4>Sources</h4>{items.map((item, index) => <article key={`${item.title}-${index}`}><div><strong>{item.document_title ?? item.title ?? "NovaCart document"}</strong><span className={item.valid === false ? "invalid" : "valid"}>{item.valid === false ? "Not validated" : "Validated"}</span></div><p>{[item.section, item.page ? `Page ${item.page}` : null].filter(Boolean).join(" · ")}</p>{item.snippet && <blockquote>{item.snippet}</blockquote>}</article>)}</aside>;
}

export function MessageList({ messages }: { messages: Message[] }) {
  if (!messages.length) return <div className="empty" tabIndex={0}><h2>Start a conversation</h2><p>Ask about an order, delivery, return, product, or policy.</p></div>;
  return <ol className="messages" aria-live="polite" tabIndex={0}>{messages.map((message) => {
    const data = structured(message.content);
    const citations = (data?.citations ?? []) as Citation[];
    const isProvider = message.role === "system_event";
    const body = typeof data?.body === "string" ? data.body : typeof data?.answer === "string" ? data.answer : message.content;
    return <li key={message.id} className={`message ${message.role}`}><header>{message.role === "customer" ? "You" : message.role === "assistant" ? "Nova" : message.role === "staff" ? "NovaCart support" : "Order update"}<time>{new Date(message.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></header>{isProvider && data ? <ProviderEvent data={data} /> : <p>{body}</p>}<Citations items={citations} /></li>;
  })}</ol>;
}

function ProviderEvent({ data }: { data: Record<string, unknown> }) {
  const state = (data.state ?? {}) as Record<string, unknown>;
  if (data.type === "crm_public_reply") return <p>{String(data.body ?? "A support reply was added.")}</p>;
  return <div className="provider-grid"><span><small>Status</small>{String(state.status ?? "Updated")}</span><span><small>Fulfillment</small>{String(state.fulfillment_status ?? "—")}</span><span><small>Tracking</small>{String(state.tracking_status ?? "—")}</span></div>;
}
