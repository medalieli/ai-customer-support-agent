import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import Home from "@/app/page";

it("renders the NovaCart support login", () => {
  render(<Home />);
  expect(
    screen.getByRole("heading", { name: "How can we help?" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Customer" })).toBeInTheDocument();
  expect(screen.getByText(/sample customer and order information/)).toBeInTheDocument();
});
