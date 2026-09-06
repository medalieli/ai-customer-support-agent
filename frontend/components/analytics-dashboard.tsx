"use client";

import { useEffect, useState } from "react";
import { Analytics, api, ApiProblem } from "@/lib/api";

const ranges = ["24h", "7d", "30d", "90d"] as const;

export function AnalyticsDashboard() {
  const [range, setRange] = useState<(typeof ranges)[number]>("7d");
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    api<Analytics>(`/staff/analytics?range=${range}`)
      .then((value) => { setData(value); setLoading(false); })
      .catch((problem: ApiProblem) => { setError(problem.message); setLoading(false); });
  }, [range]);
  return <section className="analytics" aria-labelledby="analytics-heading">
    <header><div><p className="eyebrow">Operational analytics</p><h1 id="analytics-heading">Service overview</h1></div><label>Time range<select value={range} onChange={(event) => { setRange(event.target.value as typeof range); setLoading(true); setError(""); }}>{ranges.map((value) => <option key={value}>{value}</option>)}</select></label></header>
    {error ? <div className="alert" role="alert">Analytics are temporarily unavailable. {error}</div> : loading || !data ? <p role="status">Loading operational analytics…</p> : <>
      <div className="metric-grid">
        <Metric label="Conversations" value={data.conversation_volume} />
        <Metric label="Contained" value={`${(data.containment_rate * 100).toFixed(1)}%`} />
        <Metric label="Escalated" value={`${(data.escalation_rate * 100).toFixed(1)}%`} />
        <Metric label="Tool success" value={`${(data.tool_success_rate * 100).toFixed(1)}%`} />
        <Metric label="Average response" value={`${data.average_response_latency_ms} ms`} />
        <Metric label="P95 response" value={`${data.p95_response_latency_ms} ms`} />
        <Metric label="Citation success" value={`${(data.citation_success_rate * 100).toFixed(1)}%`} />
      </div>
      <div className="analytics-groups"><Group title="Confirmation funnel" values={data.confirmations} /><Group title="Workflows" values={data.aggregates} /><Group title="Provider health" values={data.provider_health} /><Group title="Webhooks" values={data.webhooks} /></div>
      {!data.conversation_volume && <p className="privacy">No conversations occurred in this time range.</p>}
    </>}
  </section>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <article><span>{label}</span><strong>{value}</strong></article>;
}

function Group({ title, values }: { title: string; values: Record<string, number> }) {
  return <article><h2>{title}</h2><dl>{Object.entries(values).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{value}</dd></div>)}</dl></article>;
}
