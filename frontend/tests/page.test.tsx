import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import Home from "@/app/page";

vi.mock("@/components/api-status", () => ({ ApiStatus: () => <div>API status placeholder</div> }));

it("renders the M1 project foundation", () => {
  render(<Home />);
  expect(
    screen.getByRole("heading", { name: "Customer support platform foundation" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/intentionally not implemented yet/i)).toBeInTheDocument();
  expect(screen.getByText(/Mock providers/)).toBeInTheDocument();
});
