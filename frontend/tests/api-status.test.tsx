import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiStatus } from "@/components/api-status";

describe("ApiStatus", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows loading then healthy", async () => {
    let resolveFetch: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(() => pending));

    render(<ApiStatus />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking API status");
    resolveFetch(new Response("{}", { status: 200 }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("ready"));
  });

  it("shows unavailable for a non-ready API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
    render(<ApiStatus />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("unavailable"));
  });

  it("shows unavailable for a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new Error("offline"))));
    render(<ApiStatus />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("unavailable"));
  });
});
