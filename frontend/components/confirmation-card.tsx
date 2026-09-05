/* eslint-disable react-hooks/set-state-in-effect */
"use client";
import { useEffect, useState } from "react";
import type { Confirmation } from "@/lib/api";

export function ConfirmationCard({ confirmation, busy, onDecision }: { confirmation: Confirmation; busy: boolean; onDecision: (decision: "approve" | "deny") => void }) {
  const [expired, setExpired] = useState(!confirmation.expires_at);
  useEffect(() => { document.querySelector(".confirmation .actions")?.scrollIntoView?.({ block: "nearest" }); }, [confirmation.action_id]);
  useEffect(() => {
    if (!confirmation.expires_at) { setExpired(true); return; }
    const remaining = new Date(confirmation.expires_at).getTime() - Date.now();
    if (remaining <= 0) { setExpired(true); return; }
    setExpired(false);
    const timer = window.setTimeout(() => setExpired(true), Math.min(remaining, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [confirmation.expires_at]);
  return <section className={`confirmation ${expired ? "expired" : ""}`} aria-labelledby="confirmation-title"><p className="eyebrow">Secure confirmation</p><h2 id="confirmation-title">Review before continuing</h2>{confirmation.order_number && <p><strong>Order:</strong> {confirmation.order_number}</p>}<dl><div><dt>Current</dt><dd><pre>{JSON.stringify(confirmation.masked_current_address ?? confirmation.item ?? "Not applicable", null, 2)}</pre></dd></div><div><dt>Proposed</dt><dd><pre>{JSON.stringify(confirmation.proposed_address ?? confirmation.amount ?? confirmation.fields_to_store, null, 2)}</pre></dd></div></dl>{confirmation.purpose && <p>{confirmation.purpose}</p>}<ul>{confirmation.consequences?.map((item) => <li key={item}>{item}</li>)}</ul><p className="expiry">{expired ? "This confirmation has expired. Refresh and request a new preview." : `Expires ${new Date(confirmation.expires_at!).toLocaleTimeString()}`}</p><div className="actions"><button className="primary" disabled={busy || expired} onClick={() => onDecision("approve")}>{busy ? "Confirming…" : "Approve"}</button><button disabled={busy || expired} onClick={() => onDecision("deny")}>Cancel</button></div></section>;
}
