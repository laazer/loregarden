import { fireEvent, render, screen, within } from "@testing-library/react";

import type { GraphNode } from "../../../api/memoryApi";
import { MapOverview } from "../MapOverview";

const record = (i: number): GraphNode => ({
  id: `n${i}`,
  title: `Record ${i}`,
  excerpt: "",
  node_type: "learning",
  tags: [],
  ticket_id: "",
  discredited: false,
  created_at: "2026-09-01T00:00:00",
  updated_at: "2026-09-01T00:00:00",
  origin_kind: null,
  origin_ref: null,
} as GraphNode);

describe("MapOverview groups", () => {
  it("opens a big group on its first dozen, with the rest one click away", () => {
    const nodes = Array.from({ length: 30 }, (_, i) => record(i));
    render(
      <MapOverview
        nodes={nodes}
        relations={[]}
        inferred={[{ kind: "same_ticket", key: "t1", label: "lg-ticket-1", node_ids: nodes.map((n) => n.id) }]}
        truncated={false}
        onSelect={jest.fn()}
      />,
    );
    const group = screen.getByText("lg-ticket-1").closest("details")!;

    expect(within(group).getAllByRole("listitem")).toHaveLength(12);
    const more = within(group).getByRole("button", { name: "Show all 30" });
    expect(more).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(more);
    expect(within(group).getAllByRole("listitem")).toHaveLength(30);
    expect(within(group).getByRole("button", { name: "Show fewer" })).toHaveAttribute("aria-expanded", "true");
  });

  it("shows a small group whole, with no button", () => {
    const nodes = Array.from({ length: 4 }, (_, i) => record(i));
    render(
      <MapOverview
        nodes={nodes}
        relations={[]}
        inferred={[{ kind: "same_ticket", key: "t1", label: "lg-ticket-1", node_ids: nodes.map((n) => n.id) }]}
        truncated={false}
        onSelect={jest.fn()}
      />,
    );
    const group = screen.getByText("lg-ticket-1").closest("details")!;
    expect(within(group).getAllByRole("listitem")).toHaveLength(4);
    expect(within(group).queryByRole("button", { name: /show all/i })).not.toBeInTheDocument();
  });
});
