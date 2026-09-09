"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, ApiProblem, Identity } from "@/lib/api";
import { Brand } from "@/components/brand";

type Persona = {
  persona_key: string;
  display_name: string;
  locale: string;
  organization_slug: string;
};

export default function Login() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [mode, setMode] = useState<"customer" | "staff">("customer");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<Persona[]>("/auth/demo-personas")
      .then(setPersonas)
      .catch(() =>
        setError(
          "Demo customers could not be loaded. Refresh after the API is ready.",
        ),
      );
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const [customerOrganization, persona] = String(data.get("persona")).split(
      "|",
    );
    try {
      const me =
        mode === "customer"
          ? await api<Identity>("/auth/demo-login", {
              method: "POST",
              body: JSON.stringify({
                organization_slug: customerOrganization,
                persona_key: persona,
              }),
            })
          : await api<Identity>("/auth/staff-login", {
              method: "POST",
              body: JSON.stringify({
                organization_slug: String(data.get("organization"))
                  .trim()
                  .toLowerCase(),
                email: String(data.get("email")).trim().toLowerCase(),
                password: data.get("password"),
              }),
            });
      location.assign(me.kind === "staff" ? "/staff" : "/chat");
    } catch (problem) {
      setBusy(false);
      setError((problem as ApiProblem).message);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-story">
        <Brand subtitle="Customer service" />
        <div>
          <p className="eyebrow">NOVACART SUPPORT</p>
          <h1>How can we help?</h1>
          <p>
            Check an order, arrange a return, or ask our team a question.
            Your conversations stay in one place, so you can pick up where you left off.
          </p>
          <dl className="support-services"><div><dt>Orders & delivery</dt><dd>Check order details and delivery updates.</dd></div><div><dt>Returns & refunds</dt><dd>Understand your options and the next steps.</dd></div><div><dt>Contact our team</dt><dd>Get help from a customer service specialist.</dd></div></dl>
        </div>
        <footer>English & Français <span>Demo environment</span></footer>
      </section>
      <section className="login-panel">
        <div className="login-card">
          <p className="eyebrow">YOUR ACCOUNT</p>
          <h2>{mode === "customer" ? "Customer support" : "Staff sign in"}</h2>
          <p className="login-description">{mode === "customer" ? "Select a demo customer to view conversations and request help." : "Sign in with your staff account to manage customer requests."}</p>
          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "customer"}
              onClick={() => setMode("customer")}
            >
              Customer
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "staff"}
              onClick={() => setMode("staff")}
            >
              Staff
            </button>
          </div>
          <form onSubmit={submit}>
            {mode === "customer" ? (
              <label>
                Demo customer
                <select name="persona" required>
                  {personas.map((item) => (
                    <option
                      key={`${item.organization_slug}-${item.persona_key}`}
                      value={`${item.organization_slug}|${item.persona_key}`}
                    >
                      {item.display_name} · {item.locale.toUpperCase()}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <>
                <label>
                  Organization
                  <input name="organization" defaultValue="novacart" required />
                </label>
                <label>
                  Email
                  <input
                    name="email"
                    type="email"
                    defaultValue="support@novacart.test"
                    required
                  />
                </label>
                <label>
                  Password
                  <input
                    name="password"
                    type="password"
                    required
                    minLength={8}
                  />
                </label>
              </>
            )}
            {error && (
              <div className="alert" role="alert">
                {error}
              </div>
            )}
            <button className="primary wide" disabled={busy || (mode === "customer" && !personas.length)}>{busy ? "Connecting…" : "Continue securely"}</button>
          </form>
          <p className="privacy">
            This demonstration uses sample customer and order information.
          </p>
        </div>
      </section>
    </main>
  );
}
