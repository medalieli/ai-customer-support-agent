/* eslint-disable react-hooks/set-state-in-effect, @next/next/no-location-assign-relative-destination */
"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  ApiProblem,
  AuditTimelineItem,
  AuditTimelinePage,
  idempotencyKey,
  Identity,
  Message,
  Ticket,
} from "@/lib/api";
import { MessageList } from "./message-list";
import { Brand } from "./brand";
import { AnalyticsDashboard } from "./analytics-dashboard";

function label(value: string) {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function textValues(value: unknown): string[] {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) return value.flatMap(textValues);
  if (typeof value === "object")
    return Object.values(value).flatMap(textValues);
  return [String(value)];
}

function ReadableValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "")
    return <span>Not provided</span>;
  if (Array.isArray(value))
    return value.length ? (
      <ul>
        {value.map((item, index) => (
          <li key={index}>
            <ReadableValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <span>None</span>
    );
  if (typeof value === "object")
    return (
      <dl className="summary-fields">
        {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
          <div key={key}>
            <dt>{label(key)}</dt>
            <dd>
              <ReadableValue value={item} />
            </dd>
          </div>
        ))}
      </dl>
    );
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  return <span>{String(value)}</span>;
}

export function StaffWorkspace() {
  const [tickets, setTickets] = useState<Ticket[]>([]),
    [active, setActive] = useState<Ticket | null>(null),
    [identity, setIdentity] = useState<Identity | null>(null),
    [messages, setMessages] = useState<Message[]>([]),
    [audit, setAudit] = useState<AuditTimelineItem[]>([]),
    [auditCursor, setAuditCursor] = useState<string | null>(null),
    [filter, setFilter] = useState("open"),
    [search, setSearch] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [showAnalytics, setShowAnalytics] = useState(false);
  const activeTicketId = active?.id;
  const refresh = useCallback(async () => {
    const rows = await api<Ticket[]>(
      `/staff/tickets${filter === "all" ? "" : `?status=${filter}`}`,
    );
    setTickets(rows);
    if (activeTicketId) {
      let selected = rows.find((item) => item.id === activeTicketId);
      if (!selected) {
        try {
          selected = await api<Ticket>(`/staff/tickets/${activeTicketId}`);
        } catch (problem) {
          if ((problem as ApiProblem).status !== 404) throw problem;
        }
      }
      const latest = selected;
      setActive((current) => current?.id !== activeTicketId ? current
        : latest && current.version > latest.version ? current : latest ?? null);
    }
  }, [filter, activeTicketId]);
  const loadAudit = useCallback(async (ticket: Ticket, cursor?: string) => {
    const page = await api<AuditTimelinePage>(
      `/staff/tickets/${ticket.id}/audit?limit=20${cursor ? `&cursor=${cursor}` : ""}`,
    );
    setAudit((old) => (cursor ? [...old, ...page.items] : page.items));
    setAuditCursor(page.next_cursor);
  }, []);
  useEffect(() => {
    api<Identity>("/auth/me")
      .then((current) => {
        if (current.kind !== "staff") window.location.assign("/");
        else setIdentity(current);
      })
      .catch(() => window.location.assign("/"));
  }, []);
  useEffect(() => {
    refresh().catch(() => setError("Ticket queue unavailable."));
  }, [filter]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (active)
      Promise.all([
        api<Message[]>(
          `/conversations/${active.conversation_id}/messages?limit=100`,
        ).then(setMessages),
        loadAudit(active),
      ]).catch(() => setError("Ticket details are unavailable."));
  }, [active?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const timer = window.setInterval(() => {
      void refresh().catch(() => setError("Ticket queue unavailable."));
      if (active)
        void api<Message[]>(
          `/conversations/${active.conversation_id}/messages?limit=100`,
        ).then(setMessages);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [active, refresh]);
  const shown = useMemo(
    () =>
      tickets.filter((item) =>
        [
          item.customer_name,
          item.customer_email,
          item.reason_code,
          item.priority,
          item.status,
          ...textValues(item.summary),
        ]
          .join(" ")
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [tickets, search],
  );
  const customerGroups = useMemo(
    () =>
      Array.from(
        shown
          .reduce((groups, ticket) => {
            const group = groups.get(ticket.customer_id) ?? {
              customer: ticket,
              tickets: [] as Ticket[],
            };
            group.tickets.push(ticket);
            groups.set(ticket.customer_id, group);
            return groups;
          }, new Map<string, { customer: Ticket; tickets: Ticket[] }>())
          .values(),
      ),
    [shown],
  );
  async function transition(ticket: Ticket, name: string, content?: string) {
    return api<Ticket>(`/staff/tickets/${ticket.id}/${name}`, {
      method: "POST",
      headers: {
        "Idempotency-Key": idempotencyKey(`ticket-${ticket.id}-${name}`),
      },
      body: JSON.stringify({ version: ticket.version, content }),
    });
  }
  async function action(name: string, content?: string) {
    if (!active) return;
    setBusy(true);
    setError("");
    try {
      let current = active;
      if (
        (name === "reply" || name === "note") &&
        current.status === "open" &&
        !current.assigned_staff_id
      )
        current = await transition(current, "claim");
      const updated = await transition(current, name, content);
      setActive(updated);
      setTickets((rows) =>
        rows.map((item) => (item.id === updated.id ? updated : item)),
      );
      setMessages(
        await api<Message[]>(
          `/conversations/${updated.conversation_id}/messages?limit=100`,
        ),
      );
      await loadAudit(updated);
      return true;
    } catch (problem) {
      const issue = problem as ApiProblem;
      setError(
        issue.status === 409
          ? `Action unavailable: ${label(issue.code)}. Select an open, unassigned ticket or a ticket assigned to you.`
          : issue.message,
      );
      await refresh();
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function send(event: FormEvent<HTMLFormElement>, name: "reply" | "note") {
    event.preventDefault();
    const element = event.currentTarget,
      form = new FormData(element),
      content = String(form.get("content") ?? "").trim();
    if (content && (await action(name, content))) element.reset();
  }
  async function deleteTicket() {
    if (!active || busy || !window.confirm("Delete this ticket from the staff queue? The conversation and audit records will be retained. An administrator can recover the ticket.")) return;
    setBusy(true);
    try {
      await api(`/staff/tickets/${active.id}?version=${active.version}`, { method: "DELETE" });
      setTickets((current) => current.filter((ticket) => ticket.id !== active.id));
      setActive(null); setMessages([]); setAudit([]); setError("");
    } catch (problem) { setError((problem as ApiProblem).message); }
    finally { setBusy(false); }
  }
  const isOwner = Boolean(
      active && identity && active.assigned_staff_id === identity.subject_id,
    ),
    canClaim = Boolean(active?.status === "open" && !active.assigned_staff_id),
    canWork = Boolean(active?.status === "in_progress" && isOwner),
    canReturn = Boolean(
      active && isOwner && ["in_progress", "resolved"].includes(active.status),
    );
  return (
    <div className="workspace staff">
      <aside className="sidebar">
        <Brand subtitle="Support workspace" />
        <button
          className={showAnalytics ? "ticket-row active" : "ticket-row"}
          onClick={() => setShowAnalytics(true)}
        >
          <span>
            <strong>Analytics</strong>
            <small>Operational health</small>
          </span>
        </button>
        <label className="search">
          Search customers and tickets
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Customer, order, issue, status"
          />
        </label>
        <label className="search">
          Queue status
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">All tickets</option>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </select>
        </label>
        <nav aria-label="Tickets grouped by customer">
          {customerGroups.map(({ customer, tickets: customerTickets }) => (
            <section
              className="customer-ticket-group"
              key={customer.customer_id}
              aria-labelledby={`customer-${customer.customer_id}`}
            >
              <header>
                <strong id={`customer-${customer.customer_id}`}>
                  {customer.customer_name}
                </strong>
                <small>{customer.customer_email}</small>
                <span>
                  {customerTickets.length}{" "}
                  {customerTickets.length === 1 ? "ticket" : "tickets"}
                </span>
              </header>
              {customerTickets.map((ticket) => (
                <button
                  className={
                    !showAnalytics && active?.id === ticket.id
                      ? "ticket-row active"
                      : "ticket-row"
                  }
                  key={ticket.id}
                  onClick={() => {
                    setActive(ticket);
                    setShowAnalytics(false);
                    setError("");
                  }}
                >
                  <span>
                    <strong>{label(ticket.reason_code)}</strong>
                    <small>
                      {label(ticket.priority)} priority · {label(ticket.status)}
                    </small>
                  </span>
                  <i>#{ticket.id.slice(0, 8)}</i>
                </button>
              ))}
            </section>
          ))}
        </nav>
      </aside>
      <main className="ticket-detail">
        {showAnalytics ? (
          <AnalyticsDashboard />
        ) : active ? (
          <>
            <header className="topbar">
              <div>
                <p className="eyebrow">
                  {active.customer_name} · Ticket #{active.id.slice(0, 8)}
                </p>
                <h1>{label(active.reason_code)}</h1>
                <p>
                  {label(active.priority)} priority · {label(active.status)}
                  {isOwner
                    ? " · Assigned to you"
                    : active.assigned_staff_id
                      ? " · Assigned to another staff member"
                      : " · Unassigned"}
                </p>
              </div>
              <div className="actions">
                <button className="danger-button" disabled={busy || !["resolved", "closed"].includes(active.status)} title="Close or resolve the ticket before deleting it" onClick={deleteTicket}>Delete ticket</button>
                <button
                  disabled={busy || !canClaim}
                  title={
                    canClaim
                      ? "Assign this ticket to yourself"
                      : "Claim is available for unassigned open tickets"
                  }
                  onClick={() => action("claim")}
                >
                  Claim
                </button>
                <button
                  disabled={busy || !canWork}
                  title={
                    canWork
                      ? "Resolve this ticket"
                      : "Claim the ticket before resolving it"
                  }
                  onClick={() => action("resolve")}
                >
                  Resolve
                </button>
                <button
                  disabled={busy || !canWork}
                  title={
                    canWork
                      ? "Close this ticket"
                      : "Claim the ticket before closing it"
                  }
                  onClick={() => action("close")}
                >
                  Close
                </button>
                <button
                  disabled={busy || !canReturn}
                  title={
                    canReturn
                      ? "Return this conversation to the AI"
                      : "Only the assigned staff member can return this ticket"
                  }
                  onClick={() => action("return_to_ai")}
                >
                  Return to AI
                </button>
              </div>
            </header>
            {error && (
              <div className="alert" role="alert">
                {error}
              </div>
            )}
            <section className="summary-card">
              <h2>Customer and ticket details</h2>
              <dl className="customer-details">
                <div>
                  <dt>Customer</dt>
                  <dd>{active.customer_name}</dd>
                </div>
                <div>
                  <dt>Email</dt>
                  <dd>{active.customer_email}</dd>
                </div>
                <div>
                  <dt>Language</dt>
                  <dd>{active.customer_locale.toUpperCase()}</dd>
                </div>
                <div>
                  <dt>Created</dt>
                  <dd>{new Date(active.created_at).toLocaleString()}</dd>
                </div>
              </dl>
              <h3>Support summary</h3>
              <ReadableValue value={active.summary} />
            </section>
            <section className="staff-thread">
              <h2>Conversation history</h2>
              <MessageList messages={messages} />
            </section>
            <section className="audit-timeline" aria-labelledby="audit-heading">
              <h2 id="audit-heading">Audit timeline</h2>
              {audit.length ? (
                <ol>
                  {audit.map((item) => (
                    <li key={item.id}>
                      <span className={`audit-kind ${item.category}`}>
                        {item.category}
                      </span>
                      <div>
                        <strong>
                          {label(item.action.replaceAll(".", " "))}
                        </strong>
                        <p>
                          {label(item.actor_type)} · {label(item.outcome)}
                          {item.reason_code
                            ? ` · ${label(item.reason_code)}`
                            : ""}
                        </p>
                        <time dateTime={item.occurred_at}>
                          {new Date(item.occurred_at).toLocaleString()}
                        </time>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="privacy">
                  No operational events recorded for this ticket yet.
                </p>
              )}
              {auditCursor && (
                <button
                  disabled={busy}
                  onClick={() => loadAudit(active, auditCursor)}
                >
                  Load earlier events
                </button>
              )}
            </section>
            <div className="staff-composers">
              <form onSubmit={(event) => send(event, "reply")}>
                <label>
                  Public reply
                  <textarea
                    name="content"
                    required
                    maxLength={4000}
                    disabled={busy || !(canWork || canClaim)}
                  />
                </label>
                {canClaim && (
                  <p className="privacy">
                    Sending your first reply will claim this ticket
                    automatically.
                  </p>
                )}
                <button
                  className="primary"
                  disabled={busy || !(canWork || canClaim)}
                >
                  Send to customer
                </button>
              </form>
              <form
                className="private-note"
                onSubmit={(event) => send(event, "note")}
              >
                <label>
                  Private internal note
                  <textarea
                    name="content"
                    required
                    maxLength={4000}
                    disabled={busy || !(canWork || canClaim)}
                  />
                </label>
                <p>Only Support and Admin staff can see this note.</p>
                <button disabled={busy || !(canWork || canClaim)}>
                  Add private note
                </button>
              </form>
            </div>
          </>
        ) : (
          <div className="empty">
            <h1>Select a customer ticket</h1>
            <p>Choose a customer, then review one of their tickets.</p>
          </div>
        )}
      </main>
    </div>
  );
}
