"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, ApiProblem, Identity } from "@/lib/api";

type Persona = { persona_key: string; display_name: string; locale: string; organization_slug: string };

export default function Login() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [mode, setMode] = useState<"customer" | "staff">("customer");
  const [error, setError] = useState("");
  useEffect(() => { api<Identity>("/auth/me").then((me) => location.assign(me.kind === "staff" ? "/staff" : "/chat")).catch(() => api<Persona[]>("/auth/demo-personas").then(setPersonas).catch(() => setError("Demo customers could not be loaded. Refresh after the API is ready."))); }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const data = new FormData(event.currentTarget);
    const [customerOrganization, persona] = String(data.get("persona")).split("|");
    try {
      const me = mode === "customer"
        ? await api<Identity>("/auth/demo-login", { method: "POST", body: JSON.stringify({ organization_slug: customerOrganization, persona_key: persona }) })
        : await api<Identity>("/auth/staff-login", { method: "POST", body: JSON.stringify({ organization_slug: String(data.get("organization")).trim().toLowerCase(), email: String(data.get("email")).trim().toLowerCase(), password: data.get("password") }) });
      location.assign(me.kind === "staff" ? "/staff" : "/chat");
    } catch (problem) { setError((problem as ApiProblem).message); }
  }

  return <main className="login-shell"><section className="login-story"><div className="brand light"><span>N</span><div><strong>NovaCart</strong><small>Shop well. Supported always.</small></div></div><div><p className="eyebrow">Thoughtful support, from checkout onward</p><h1>Answers that understand your order.</h1><p>Track deliveries, resolve order questions, and reach a real NovaCart specialist in one secure conversation.</p></div><footer>Secure synthetic demonstration · English & Français</footer></section><section className="login-panel"><div className="login-card"><p className="eyebrow">Welcome</p><h2>Sign in to support</h2><div className="tabs" role="tablist"><button type="button" role="tab" aria-selected={mode === "customer"} onClick={() => setMode("customer")}>Customer</button><button type="button" role="tab" aria-selected={mode === "staff"} onClick={() => setMode("staff")}>Staff</button></div><form onSubmit={submit}>{mode === "customer" ? <label>Demo customer<select name="persona" required>{personas.map((item) => <option key={`${item.organization_slug}-${item.persona_key}`} value={`${item.organization_slug}|${item.persona_key}`}>{item.display_name} · {item.locale.toUpperCase()}</option>)}</select></label> : <><label>Organization<input name="organization" defaultValue="novacart" required /></label><label>Email<input name="email" type="email" defaultValue="support@novacart.test" required /></label><label>Password<input name="password" type="password" required minLength={8} /></label></>}{error && <div className="alert" role="alert">{error}</div>}<button className="primary wide">Continue securely</button></form><p className="privacy">Your session stays in a secure HttpOnly cookie. NovaCart never stores browser tokens.</p></div></section></main>;
}
