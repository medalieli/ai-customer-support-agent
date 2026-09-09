/* eslint-disable react-hooks/set-state-in-effect, @next/next/no-location-assign-relative-destination */
"use client";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  AgentRun,
  ApiProblem,
  Confirmation,
  Conversation,
  idempotencyKey,
  Message,
  replaySse,
} from "@/lib/api";
import { ConfirmationCard } from "./confirmation-card";
import { MessageList } from "./message-list";
import { Brand } from "./brand";

export function CustomerWorkspace() {
  const [search, setSearch] = useState("");
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const threadEnd = useRef<HTMLDivElement>(null);
  const [deleting, setDeleting] = useState(false);
  const [pendingMessage, setPendingMessage] = useState<Message | null>(null);
  const requestInFlight = useRef(false);
  const [conversations, setConversations] = useState<Conversation[]>([]),
    [active, setActive] = useState<Conversation | null>(null),
    [messages, setMessages] = useState<Message[]>([]);
  const [confirmation, setConfirmation] = useState<{
      value: Confirmation;
      version: number;
    } | null>(null),
    [state, setState] = useState<
      "idle" | "sending" | "streaming" | "reconnecting" | "offline" | "error"
    >("idle"),
    [error, setError] = useState("");
  const cursor = useRef(0),
    seen = useRef(new Set<number>());
  const decisionKeys = useRef(new Map<string, string>());
  const busy = deleting || ["sending", "streaming", "reconnecting"].includes(state);
  const visibleMessages = pendingMessage && !messages.some((message) =>
    message.role === "customer" && message.content === pendingMessage.content &&
    message.sequence_number >= pendingMessage.sequence_number)
    ? [...messages, pendingMessage] : messages;
  useEffect(() => {
    threadEnd.current?.scrollIntoView({ behavior: "auto", block: "end" });
  }, [messages, pendingMessage, state]);
  useEffect(() => {
    setConfirmation(null);
  }, [active?.id]);
  const refreshMessages = useCallback(
    async (conversation = active) => {
      if (!conversation) return;
      const result = await api<Message[]>(
        `/conversations/${conversation.id}/messages?limit=100`,
      );
      setMessages(result);
    },
    [active],
  );
  const refresh = useCallback(async () => {
    const result = await api<Conversation[]>("/conversations");
    setConversations(result);
    if (!active && result[0]) setActive(result[0]);
  }, [active]);
  useEffect(() => {
    refresh().catch(() => setError("Unable to load conversations."));
  }, [refresh]);
  useEffect(() => {
    let cancelled = false;
    if (active)
      api<Message[]>(`/conversations/${active.id}/messages?limit=100`)
        .then((items) => {
          if (!cancelled) setMessages(items);
        })
        .catch(() => {
          if (!cancelled) setError("Unable to load this conversation.");
        });
    return () => {
      cancelled = true;
    };
  }, [active?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!active || active.ownership_state === "ai_active") return;
    const timer = window.setInterval(() => {
      void refreshMessages(active);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [active, refreshMessages]);
  useEffect(() => {
    const online = () => setState("idle"),
      offline = () => setState("offline");
    addEventListener("online", online);
    addEventListener("offline", offline);
    return () => {
      removeEventListener("online", online);
      removeEventListener("offline", offline);
    };
  }, []);

  async function createConversation() {
    const value = await api<Conversation>("/conversations", {
      method: "POST",
      body: JSON.stringify({ title: "New conversation" }),
    });
    setConversations((old) => [value, ...old]);
    setActive(value);
    setMessages([]);
  }
  async function deleteConversation() {
    if (!active || busy || !window.confirm("Delete this conversation from your history? Any linked support ticket will remain available to staff. A retained record can be recovered by an administrator.")) return;
    setDeleting(true);
    try {
      await api(`/conversations/${active.id}`, { method: "DELETE" });
      const remaining = conversations.filter((item) => item.id !== active.id);
      setConversations(remaining); setMessages([]); setPendingMessage(null);
      setConfirmation(null); setActive(remaining[0] ?? null); setError(""); setState("idle");
    } catch (problem) { setError((problem as ApiProblem).message); setState("error"); }
    finally { setDeleting(false); }
  }
  async function stream(run: AgentRun) {
    cursor.current = 0;
    seen.current.clear();
    setState("streaming");
    for (let attempt = 0; attempt < 3; attempt++)
      try {
        const events = await replaySse(run.run_id, cursor.current);
        for (const event of events)
          if (!seen.current.has(event.id)) {
            seen.current.add(event.id);
            cursor.current = Math.max(cursor.current, event.id);
          }
        await refreshMessages();
        const completed = events.findLast(
          (event) => event.type === "response_completed",
        );
        if (completed && typeof completed.data.message === "string")
          setMessages((current) => {
            const index = current.findLastIndex(
              (item) => item.role === "assistant",
            );
            return current.map((item, position) =>
              position === index
                ? {
                    ...item,
                    content: JSON.stringify({
                      answer: completed.data.message,
                      citations: completed.data.citations ?? [],
                    }),
                  }
                : item,
            );
          });
        setState("idle");
        return;
      } catch {
        setState("reconnecting");
        await new Promise((resolve) =>
          setTimeout(resolve, 300 * (attempt + 1)),
        );
      }
    setState("error");
    setError(
      "Live updates disconnected. Your message is saved; refresh to reconnect.",
    );
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active || busy || requestInFlight.current) return;
    const form = new FormData(event.currentTarget),
      content = String(form.get("message") ?? "").trim();
    if (!content) return;
    requestInFlight.current = true;
    setPendingMessage({ id: `pending-${crypto.randomUUID()}`, role: "customer", content,
      sequence_number: Math.max(0, ...messages.map((message) => message.sequence_number)) + 1,
      locale: active.locale, created_at: new Date().toISOString() });
    setState("sending");
    setError("");
    event.currentTarget.reset();
    try {
      const run = await api<AgentRun>(`/agent/threads/${active.id}/messages`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey(`message-${active.id}`) },
        body: JSON.stringify({ content }),
      });
      if (run.confirmation)
        setConfirmation({
          value: run.confirmation,
          version: run.checkpoint_version,
        });
      await refreshMessages();
      setPendingMessage(null);
      await stream(run);
      const updated = await api<Conversation>(`/conversations/${active.id}`);
      setActive((current) =>
        current &&
        current.status === updated.status &&
        current.ownership_state === updated.ownership_state
          ? current
          : updated,
      );
    } catch (problem) {
      setState("error");
      setError(`${(problem as ApiProblem).message ?? "The connection was interrupted."} Refresh the conversation to check whether your message was received.`);
    } finally {
      requestInFlight.current = false;
    }
  }
  async function decide(decision: "approve" | "deny") {
    if (!active || !confirmation || state === "sending") return;
    const scope = `confirm-${confirmation.value.action_id}-${decision}`;
    const key = decisionKeys.current.get(scope) ?? idempotencyKey(scope);
    decisionKeys.current.set(scope, key);
    setState("sending");
    setError("");
    try {
      const run = await api<AgentRun>(`/agent/threads/${active.id}/resume`, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify({
          checkpoint_version: confirmation.version,
          action_id: confirmation.value.action_id,
          confirmation_token: confirmation.value.confirmation_token,
          decision,
          value: { decision },
        }),
      });
      setConfirmation(null);
      await refreshMessages();
      if (run.result?.reason_code === "expired")
        throw { message: "This confirmation expired. Request a new preview." };
      const expected =
        decision === "approve"
          ? ["action_completed", "refund_request_submitted"]
          : ["action_cancelled"];
      if (!expected.includes(run.status))
        throw { message: "The provider did not confirm the change." };
      setState("idle");
    } catch (problem) {
      const failure = problem as ApiProblem;
      setError(
        failure.status === 409
          ? "The preview is stale or conflicted. Server state has been refreshed."
          : (failure.message ??
              "Confirmation could not be verified. Retry to check the same request."),
      );
      if (failure.status && failure.status < 500) setConfirmation(null);
      try {
        await refreshMessages();
      } finally {
        setState("error");
      }
    }
  }
  const handoff = active?.ownership_state ?? "ai_active";
  return (
    <div className="workspace">
      <aside className="sidebar">
        <Brand subtitle="Customer care" />
        <button className="primary wide" disabled={busy} onClick={() => { setPendingMessage(null); void createConversation(); }}>
          New conversation
        </button>
        <label className="search">Find a conversation<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search conversations…" /></label>
        <p className="section-label">Your conversations · {conversations.length}</p>
        <nav aria-label="Conversations">
          {conversations.filter((item) => (item.title ?? "Support conversation").toLowerCase().includes(search.toLowerCase())).map((item) => (
            <button
              key={item.id}
              disabled={busy}
              className={
                active?.id === item.id ? "nav-item active" : "nav-item"
              }
              onClick={() => { setPendingMessage(null); setActive(item); }}
            >
              <strong>{item.title ?? "Support conversation"}</strong>
              <small>{new Date(item.created_at).toLocaleDateString()}</small>
            </button>
          ))}
        </nav>
        <button
          className="text-button"
          onClick={() =>
            api("/auth/logout", { method: "POST" }).then(() => {
              window.location.href = "/";
            })
          }
        >
          Sign out
        </button>
      </aside>
      <main className="chat">
        <header className="topbar">
          <div>
            <p className="eyebrow">NovaCart support</p>
            <h1>{active?.title ?? "How can we help?"}</h1>
          </div>
          <div className="actions"><button className="danger-button" disabled={!active || busy} onClick={deleteConversation}>Delete conversation</button><span className={`ownership ${handoff}`}>
            {handoff.replaceAll("_", " ")}
          </span></div>
        </header>
        <section className="thread">
          <MessageList messages={visibleMessages} />
          {busy && <div className="typing-indicator" aria-live="polite"><span className="typing-dots" aria-hidden="true"><i /><i /><i /></span>{active?.ownership_state === "ai_active" ? "Nova is preparing a reply…" : "Sending your message…"}</div>}
          {!visibleMessages.length && <div className="quick-actions">{[
            ["Track an order", "Where is my order?", "Follow your delivery"],
            ["Returns & refunds", "How can I return an item?", "Find your next step"],
            ["Product questions", "Can you help me choose a product?", "Ask about features and availability"],
            ["Talk to a person", "I would like to speak to a human agent.", "Connect with our team"],
          ].map(([title, question, description]) => <button key={title} disabled={!active} onClick={() => { if (composerRef.current) { composerRef.current.value = question; composerRef.current.focus(); } }}>{title}<small>{description}</small></button>)}</div>}
          {confirmation && (
            <ConfirmationCard
              confirmation={confirmation.value}
              busy={state === "sending"}
              onDecision={decide}
            />
          )}
          <div ref={threadEnd} />
        </section>
        <div className={`stream-state ${state}`} role="status">
          {state === "sending" ? "Nova is working on your request…" : state === "streaming"
            ? "Nova is responding…"
            : state === "reconnecting"
              ? "Reconnecting to live updates…"
              : state === "offline"
                ? "You are offline. Drafts are not sent."
                : state === "error"
                  ? error
                  : "Connected securely"}
        </div>
        <form className="composer" onSubmit={submit}>
          <label htmlFor="message" className="sr-only">
            Message NovaCart support
          </label>
          <textarea
            ref={composerRef}
            id="message"
            name="message"
            required
            maxLength={8000}
            disabled={!active || busy || state === "offline"}
            placeholder="Ask about an order, return, or product…"
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
          <button
            className="primary"
            disabled={!active || busy || state === "offline"}
          >
            Send
          </button>
        </form>
      </main>
    </div>
  );
}
