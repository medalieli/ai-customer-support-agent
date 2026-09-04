import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ConfirmationCard } from "@/components/confirmation-card";
import { MessageList } from "@/components/message-list";

it("renders customer, staff, provider updates, and validated citations safely", () => {
  render(<MessageList messages={[
    { id: "1", sequence_number: 1, role: "customer", content: "Where is it?", locale: "en", created_at: "2026-01-01T10:00:00Z" },
    { id: "2", sequence_number: 2, role: "assistant", content: JSON.stringify({ answer: "It is in transit.", citations: [{ document_title: "Shipping policy", section: "Tracking", page: 2, snippet: "Tracking updates appear here.", valid: true }] }), locale: "en", created_at: "2026-01-01T10:01:00Z" },
    { id: "3", sequence_number: 3, role: "staff", content: "I am checking this.", locale: "en", created_at: "2026-01-01T10:02:00Z" },
    { id: "4", sequence_number: 4, role: "system_event", content: JSON.stringify({ type: "provider_sync", state: { status: "open", fulfillment_status: "fulfilled", tracking_status: "in_transit" } }), locale: "en", created_at: "2026-01-01T10:03:00Z" },
  ]}/>);
  expect(screen.getByText("It is in transit.")).toBeInTheDocument();
  expect(screen.getByText("Validated")).toBeInTheDocument();
  expect(screen.getByText("in_transit")).toBeInTheDocument();
});

it("requires an explicit confirmation decision and disables expired actions", () => {
  const decide = vi.fn();
  const { rerender } = render(<ConfirmationCard busy={false} onDecision={decide} confirmation={{ action_id: "a", confirmation_token: "x".repeat(32), expires_at: "2099-01-01T00:00:00Z", order_number: "NC-1001", proposed_address: { city: "Portland" }, consequences: ["The provider must confirm the write."] }}/>);
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(decide).toHaveBeenCalledWith("approve");
  rerender(<ConfirmationCard busy={false} onDecision={decide} confirmation={{ action_id: "a", confirmation_token: "x".repeat(32), expires_at: null }}/>);
  expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
});
