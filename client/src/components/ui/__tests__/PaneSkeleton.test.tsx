/**
 * The pane placeholder: a pane that is waiting must look like a pane that is
 * waiting — not like an empty one, and not like a broken one.
 *
 * What is pinned here is behaviour, not copy: that the wait is announced once to
 * assistive tech, that the bars are hidden from it, and that a variant draws the
 * shape it claims. The label text itself is the caller's.
 */

import { render, screen } from "@testing-library/react";

import { PaneSkeleton } from "../PaneSkeleton";

function bars(container: HTMLElement) {
  return container.querySelectorAll(".pane-skeleton-bar");
}

describe("PaneSkeleton", () => {
  it("announces the wait once, as a busy status", () => {
    render(<PaneSkeleton label="Loading file…" />);

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(status).toHaveTextContent("Loading file…");
  });

  it("hides the bars from assistive tech, so the wait is not read as empty rows", () => {
    const { container } = render(<PaneSkeleton variant="list" rows={4} />);

    for (const row of container.querySelectorAll(".pane-skeleton-row")) {
      expect(row).toHaveAttribute("aria-hidden");
    }
  });

  it("draws one row per requested row", () => {
    const { container } = render(<PaneSkeleton variant="list" rows={4} />);
    expect(container.querySelectorAll(".pane-skeleton-row")).toHaveLength(4);
  });

  it("gives a code pane a gutter bar per line, and a list pane none", () => {
    const code = render(<PaneSkeleton variant="code" rows={3} />);
    expect(code.container.querySelectorAll(".pane-skeleton-gutter")).toHaveLength(3);

    const list = render(<PaneSkeleton variant="list" rows={3} />);
    expect(list.container.querySelectorAll(".pane-skeleton-gutter")).toHaveLength(0);
  });

  it("draws a block variant as one filling bar, whatever rows says", () => {
    const { container } = render(<PaneSkeleton variant="block" rows={9} />);
    expect(bars(container)).toHaveLength(1);
    expect(container.querySelector(".pane-skeleton-block")).not.toBeNull();
  });

  it("draws the same widths on re-render, so the placeholder does not flicker", () => {
    const { container, rerender } = render(<PaneSkeleton variant="list" rows={5} />);
    const before = [...bars(container)].map((bar) => bar.getAttribute("style"));

    rerender(<PaneSkeleton variant="list" rows={5} />);
    expect([...bars(container)].map((bar) => bar.getAttribute("style"))).toEqual(before);
  });

  it("never draws zero rows, however few it is asked for", () => {
    const { container } = render(<PaneSkeleton variant="list" rows={0} />);
    expect(container.querySelectorAll(".pane-skeleton-row").length).toBeGreaterThan(0);
  });
});
