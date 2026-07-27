import { render, screen } from "@testing-library/react";

import { ArtifactView } from "../ArtifactView";
import type { TicketDetail } from "../../../api/client";
import type { ContextSection } from "../../../api/types";

// Ticket 88: a passing transition gate and a skipped one used to be
// indistinguishable everywhere — the context tab renders any kind="context"
// artifact generically by title/rows, so a gate-evaluation section whose
// title names its outcome is enough to make "passed" and "skipped" read
// differently to an operator without any extra UI surface.

function makeTicket(context: ContextSection[]): TicketDetail {
  return {
    id: "t1",
    stages: [],
    blocking_issues: "",
    artifacts: { context },
  } as unknown as TicketDetail;
}

it("renders a passed gate context section distinctly from a skipped one", () => {
  const ticket = makeTicket([
    {
      title: "Gate passed — test_break",
      rows: [
        { k: "Stage", v: "test_break" },
        { k: "Outcome", v: "passed" },
        { k: "Message", v: "passed 1 gate command(s)" },
      ],
    },
  ]);

  render(<ArtifactView tab="context" ticket={ticket} />);

  expect(screen.getByText("Gate passed — test_break")).toBeInTheDocument();
  expect(screen.queryByText(/gate skipped/i)).not.toBeInTheDocument();
});

it("renders a skipped gate context section distinctly from a passed one", () => {
  const ticket = makeTicket([
    {
      title: "Gate skipped — test_break",
      rows: [
        { k: "Stage", v: "test_break" },
        { k: "Outcome", v: "skipped" },
        { k: "Message", v: "no gate commands configured" },
      ],
    },
  ]);

  render(<ArtifactView tab="context" ticket={ticket} />);

  expect(screen.getByText("Gate skipped — test_break")).toBeInTheDocument();
  expect(screen.queryByText(/gate passed/i)).not.toBeInTheDocument();
});
