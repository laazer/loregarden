/**
 * A status chip must be readable without its colour: the word is what a screen
 * reader hears and the glyph is what a colour-blind viewer sees.
 */

import { render, screen } from "@testing-library/react";

import { StatusChip } from "../StatusChip";

describe("StatusChip", () => {
  it("is announced by its word alone, with the glyph hidden from assistive tech", () => {
    const { container } = render(<StatusChip tone="bad" label="Failed" />);

    expect(screen.getByText("Failed").textContent).toBe("Failed");
    const glyph = container.querySelector(".status-chip-glyph");
    expect(glyph).toHaveAttribute("aria-hidden", "true");
  });

  it("carries its tone as a class and a data attribute, so the glyph shape follows it", () => {
    render(<StatusChip tone="you" label="Needs you" className="extra" />);

    const chip = screen.getByText("Needs you");
    expect(chip).toHaveClass("status-chip", "status-chip--you", "extra");
    expect(chip).toHaveAttribute("data-tone", "you");
  });

  it("puts detail on hover rather than in the label", () => {
    render(<StatusChip tone="run" label="Running" title="slot held 4m" />);

    expect(screen.getByText("Running")).toHaveAttribute("title", "slot held 4m");
  });
});
