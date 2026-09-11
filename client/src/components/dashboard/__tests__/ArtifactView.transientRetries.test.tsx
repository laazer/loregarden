import { render, screen } from "@testing-library/react";

import { ArtifactView } from "../ArtifactView";
import type { TicketDetail } from "../../../api/client";
import type { TransientRetryNotice } from "../../../api/types";

/**
 * A stage the control plane re-ran by itself has to be visible.
 *
 * The retry is deliberately not a block: the run died of infrastructure, the
 * stage was re-armed, and `blocking_issues` is left empty so the ticket does not
 * read as failed. That is correct, and it also means nothing else on the payload
 * says the work was attempted twice — so a stage that quietly ran five times
 * looked exactly like one that ran once. This is the surface that closes that.
 */

function makeTicket(
  transient_retries: TransientRetryNotice[],
  blocking_issues = "",
): TicketDetail {
  return {
    id: "t1",
    stages: [],
    blocking_issues,
    artifacts: { transient_retries },
  } as unknown as TicketDetail;
}

const retry = (stage_key: string, at: string, message: string): TransientRetryNotice => ({
  stage_key,
  at,
  message,
  reason: "infrastructure",
});

it("shows the retry with its stage, count and cause", () => {
  const ticket = makeTicket([
    retry(
      "implement",
      "2026-09-11T12:00:00Z",
      "Stage 'implement' hit a transient failure and was re-armed automatically (retry 1 of 5). " +
        "Cause: Agent run lease expired",
    ),
  ]);

  render(<ArtifactView tab="errors" ticket={ticket} />);

  expect(screen.getByText("Automatic retries")).toBeInTheDocument();
  expect(screen.getByText(/implement · 1 retry/)).toBeInTheDocument();
  expect(screen.getByText(/lease expired/)).toBeInTheDocument();
});

it("groups several retries of one stage into a single count", () => {
  const ticket = makeTicket([
    retry("implement", "2026-09-11T12:00:00Z", "retry 1 of 5. Cause: keychain"),
    retry("implement", "2026-09-11T12:30:00Z", "retry 2 of 5. Cause: keychain"),
    retry("verify", "2026-09-11T13:00:00Z", "retry 1 of 5. Cause: lease"),
  ]);

  render(<ArtifactView tab="errors" ticket={ticket} />);

  expect(screen.getByText(/implement · 2 retries/)).toBeInTheDocument();
  expect(screen.getByText(/verify · 1 retry/)).toBeInTheDocument();
});

it("shows the latest message for a stage, not the first", () => {
  const ticket = makeTicket([
    retry("implement", "2026-09-11T12:00:00Z", "retry 1 of 5. Cause: first cause"),
    retry("implement", "2026-09-11T12:30:00Z", "retry 2 of 5. Cause: latest cause"),
  ]);

  render(<ArtifactView tab="errors" ticket={ticket} />);

  expect(screen.getByText(/latest cause/)).toBeInTheDocument();
  expect(screen.queryByText(/first cause/)).not.toBeInTheDocument();
});

it("does not report 'no errors' when the only history is a retry", () => {
  // The empty state is the trap: a ticket whose machine failed under it twice
  // and carried on is not a ticket where nothing happened.
  const ticket = makeTicket([retry("implement", "2026-09-11T12:00:00Z", "retry 1 of 5")]);

  render(<ArtifactView tab="errors" ticket={ticket} />);

  expect(screen.queryByText(/no errors recorded/i)).not.toBeInTheDocument();
});

it("still reports the empty state when there is genuinely nothing", () => {
  render(<ArtifactView tab="errors" ticket={makeTicket([])} />);

  expect(screen.getByText(/no errors recorded/i)).toBeInTheDocument();
});

it("renders alongside a real blocking issue without replacing it", () => {
  // The exhausted case: five retries spent, then a genuine block. Both facts
  // matter, and the retries are the context that makes the block make sense.
  const ticket = makeTicket(
    [retry("implement", "2026-09-11T12:00:00Z", "retry 5 of 5")],
    "Stage 'implement' died of an infrastructure failure on 5 consecutive automatic retries.",
  );

  render(<ArtifactView tab="errors" ticket={ticket} />);

  expect(screen.getByText("Automatic retries")).toBeInTheDocument();
  expect(screen.getByText("Blocking issue")).toBeInTheDocument();
});

it("survives a retry notice with no timestamp", () => {
  const ticket = makeTicket([retry("implement", "", "retry 1 of 5")]);

  render(<ArtifactView tab="errors" ticket={ticket} />);

  expect(screen.getByText(/implement · 1 retry/)).toBeInTheDocument();
});

it("falls back to a sentence when the notice carries no message", () => {
  const ticket = makeTicket([retry("implement", "2026-09-11T12:00:00Z", "")]);

  render(<ArtifactView tab="errors" ticket={ticket} />);

  expect(screen.getByText(/Re-dispatched after an infrastructure failure/)).toBeInTheDocument();
});
