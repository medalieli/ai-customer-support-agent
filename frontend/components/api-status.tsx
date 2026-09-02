"use client";

import { useEffect, useState } from "react";

type Status = "loading" | "healthy" | "unavailable";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function ApiStatus() {
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    const controller = new AbortController();

    async function checkApi() {
      try {
        const response = await fetch(`${apiUrl}/health/ready`, {
          signal: controller.signal,
          cache: "no-store",
        });
        setStatus(response.ok ? "healthy" : "unavailable");
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setStatus("unavailable");
        }
      }
    }

    void checkApi();
    return () => controller.abort();
  }, []);

  const labels: Record<Status, string> = {
    loading: "Checking API status…",
    healthy: "API and dependencies are ready",
    unavailable: "API is currently unavailable",
  };

  return (
    <div className={`status status-${status}`} role="status" aria-live="polite">
      <span className="status-dot" aria-hidden="true" />
      {labels[status]}
    </div>
  );
}
