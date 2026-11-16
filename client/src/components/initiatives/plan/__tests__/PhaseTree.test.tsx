import { render, screen, within } from "@testing-library/react";

import type { TicketSummary } from "../../../../api/types";
import { PhaseTree } from "../PhaseTree";

const ticket = (id: string, state: TicketSummary["state"]): TicketSummary =>
  ({
    id,
    external_id: `lg-${id}`,
    title: id,
    state,
    work_item_type: "feature",
    parent_ticket_id: "m1",
  }) as TicketSummary;

function renderTree(kids: TicketSummary[]) {
  render(
    <PhaseTree
      id="tree"
      root={{ id: "m1", external_id: "lg-m1" }}
      childrenOf={new Map([["m1", kids]])}
      nodeById={new Map()}
    />,
  );
  const list = screen.getByRole("list", { name: "Work under lg-m1" });
  return within(list)
    .getAllByRole("button", { name: /^lg-/ })
    .map((b) => b.textContent?.split(" ")[0]);
}

describe("PhaseTree", () => {
  it("sorts done and won't-do tickets below open ones, keeping each group's order", () => {
    const order = renderTree([
      ticket("a", "done"),
      ticket("b", "in_progress"),
      ticket("c", "wont_do"),
      ticket("d", "backlog"),
    ]);
    expect(order).toEqual(["lg-b", "lg-d", "lg-a", "lg-c"]);
  });

  it("keeps open tickets inside the cap when done ones came first", () => {
    const done = Array.from({ length: 12 }, (_, i) => ticket(`done${i}`, "done"));
    const order = renderTree([...done, ticket("open", "backlog")]);
    expect(order[0]).toBe("lg-open");
    expect(order).toHaveLength(12);
  });
});
