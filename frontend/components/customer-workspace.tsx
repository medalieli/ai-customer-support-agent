/* eslint-disable react-hooks/set-state-in-effect, @next/next/no-location-assign-relative-destination */
"use client";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, AgentRun, ApiProblem, Confirmation, Conversation, idempotencyKey, Message, replaySse } from "@/lib/api";
import { ConfirmationCard } from "./confirmation-card";
import { MessageList } from "./message-list";

export function CustomerWorkspace() {
  const [conversations, setConversations] = useState<Conversation[]>([]), [active, setActive] = useState<Conversation | null>(null), [messages, setMessages] = useState<Message[]>([]);
  const [confirmation, setConfirmation] = useState<{ value: Confirmation; version: number } | null>(null), [state, setState] = useState<"idle" | "sending" | "streaming" | "reconnecting" | "offline" | "error">("idle"), [error, setError] = useState("");
  const cursor = useRef(0), seen = useRef(new Set<number>());
  const decisionKeys = useRef(new Map<string, string>());
  useEffect(() => { setConfirmation(null); }, [active?.id]);
  const refreshMessages = useCallback(async (conversation = active) => { if (!conversation) return; const result = await api<Message[]>(`/conversations/${conversation.id}/messages?limit=100`); setMessages(result); }, [active]);
  const refresh = useCallback(async () => { const result = await api<Conversation[]>("/conversations"); setConversations(result); if (!active && result[0]) setActive(result[0]); }, [active]);
  useEffect(() => { refresh().catch(() => setError("Unable to load conversations.")); }, [refresh]);
  useEffect(() => { let cancelled = false; if (active) api<Message[]>(`/conversations/${active.id}/messages?limit=100`).then((items) => { if (!cancelled) setMessages(items); }).catch(() => { if (!cancelled) setError("Unable to load this conversation."); }); return () => { cancelled = true; }; }, [active?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const online = () => setState("idle"), offline = () => setState("offline"); addEventListener("online", online); addEventListener("offline", offline); return () => { removeEventListener("online", online); removeEventListener("offline", offline); }; }, []);

  async function createConversation() { const value = await api<Conversation>("/conversations", { method: "POST", body: JSON.stringify({ title: "New conversation" }) }); setConversations((old) => [value, ...old]); setActive(value); setMessages([]); }
  async function stream(run: AgentRun) {
    cursor.current = 0; seen.current.clear();
    setState("streaming");
    for (let attempt = 0; attempt < 3; attempt++) try { const events = await replaySse(run.run_id, cursor.current); for (const event of events) if (!seen.current.has(event.id)) { seen.current.add(event.id); cursor.current = Math.max(cursor.current, event.id); } await refreshMessages(); const completed = events.findLast((event) => event.type === "response_completed"); if (completed && typeof completed.data.message === "string") setMessages((current) => { const index = current.findLastIndex((item) => item.role === "assistant"); return current.map((item, position) => position === index ? { ...item, content: JSON.stringify({ answer: completed.data.message, citations: completed.data.citations ?? [] }) } : item); }); setState("idle"); return; } catch { setState("reconnecting"); await new Promise((resolve) => setTimeout(resolve, 300 * (attempt + 1))); }
    setState("error"); setError("Live updates disconnected. Your message is saved; refresh to reconnect.");
  }
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!active || state === "sending") return; const form = new FormData(event.currentTarget), content = String(form.get("message") ?? "").trim(); if (!content) return; setState("sending"); setError(""); event.currentTarget.reset(); try { const run = await api<AgentRun>(`/agent/threads/${active.id}/messages`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey(`message-${active.id}`) }, body: JSON.stringify({ content }) }); if (run.confirmation) setConfirmation({ value: run.confirmation, version: run.checkpoint_version }); await refreshMessages(); await stream(run); const updated = await api<Conversation>(`/conversations/${active.id}`); setActive((current) => current && current.status === updated.status && current.ownership_state === updated.ownership_state ? current : updated); } catch (problem) { setState("error"); setError((problem as ApiProblem).message); } }
  async function decide(decision: "approve" | "deny") {
    if (!active || !confirmation || state === "sending") return;
    const scope = `confirm-${confirmation.value.action_id}-${decision}`;
    const key = decisionKeys.current.get(scope) ?? idempotencyKey(scope);
    decisionKeys.current.set(scope, key);
    setState("sending"); setError("");
    try {
      const run = await api<AgentRun>(`/agent/threads/${active.id}/resume`, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ checkpoint_version: confirmation.version, action_id: confirmation.value.action_id, confirmation_token: confirmation.value.confirmation_token, decision, value: { decision } }) });
      setConfirmation(null); await refreshMessages();
      if (run.result?.reason_code === "expired") throw { message: "This confirmation expired. Request a new preview." };
      const expected = decision === "approve" ? ["action_completed", "refund_request_submitted"] : ["action_cancelled"];
      if (!expected.includes(run.status)) throw { message: "The provider did not confirm the change." };
      setState("idle");
    } catch (problem) {
      const failure = problem as ApiProblem;
      setError(failure.status === 409 ? "The preview is stale or conflicted. Server state has been refreshed." : failure.message ?? "Confirmation could not be verified. Retry to check the same request.");
      if (failure.status && failure.status < 500) setConfirmation(null);
      try { await refreshMessages(); } finally { setState("error"); }
    }
  }
  const handoff = active?.ownership_state ?? "ai_active";
  return <div className="workspace"><aside className="sidebar"><div className="brand"><span>N</span><div><strong>NovaCart</strong><small>Customer care</small></div></div><button className="primary wide" onClick={() => createConversation()}>New conversation</button><nav aria-label="Conversations">{conversations.map((item) => <button key={item.id} className={active?.id === item.id ? "nav-item active" : "nav-item"} onClick={() => setActive(item)}><strong>{item.title ?? "Support conversation"}</strong><small>{new Date(item.created_at).toLocaleDateString()}</small></button>)}</nav><button className="text-button" onClick={() => api("/auth/logout", { method: "POST" }).then(() => { window.location.href = "/"; })}>Sign out</button></aside><main className="chat"><header className="topbar"><div><p className="eyebrow">NovaCart support</p><h1>{active?.title ?? "How can we help?"}</h1></div><span className={`ownership ${handoff}`}>{handoff.replaceAll("_", " ")}</span></header><section className="thread"><MessageList messages={messages}/>{confirmation && <ConfirmationCard confirmation={confirmation.value} busy={state === "sending"} onDecision={decide}/>}</section><div className={`stream-state ${state}`} role="status">{state === "streaming" ? "Nova is responding…" : state === "reconnecting" ? "Reconnecting to live updates…" : state === "offline" ? "You are offline. Drafts are not sent." : state === "error" ? error : "Connected securely"}</div><form className="composer" onSubmit={submit}><label htmlFor="message" className="sr-only">Message NovaCart support</label><textarea id="message" name="message" required maxLength={8000} disabled={!active || state === "sending" || state === "offline"} placeholder="Ask about an order, return, or product…"/><button className="primary" disabled={!active || state === "sending" || state === "offline"}>Send</button></form></main></div>;
}
