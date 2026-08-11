import { render, screen } from "@testing-library/react";

import type { TicketDetail } from "../../../api/client";
import { formatLocalTimestamp } from "../../../lib/timestamps";
import { ArtifactView } from "../ArtifactView";

const FAILED_AT = "2026-08-08T14:20:13.271660";

const RUNS = [
  {
    id: "run-id-1",
    run_code: "run_40a222",
    status: "failed",
    command: "codex exec",
    agent_id: "static_qa",
    stage_key: "script_review",
    stderr: "boom",
    created_at: "2026-08-08T14:19:57.440030",
    started_at: "2026-08-08T14:19:57.440030",
    finished_at: FAILED_AT,
  },
];

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t1",
    stages: [],
    blocking_issues: "",
    artifacts: {},
    ...overrides,
  } as unknown as TicketDetail;
}

it("dates a failed run in the viewer's timezone on the errors tab", () => {
  render(<ArtifactView tab="errors" ticket={makeTicket()} runs={RUNS} />);

  expect(screen.getByText(new RegExp(escapeRegExp(formatLocalTimestamp(FAILED_AT))))).toBeTruthy();
});

it("dates a run on the context tab too", () => {
  render(<ArtifactView tab="context" ticket={makeTicket()} runs={RUNS} />);

  expect(screen.getByText(new RegExp(escapeRegExp(formatLocalTimestamp(FAILED_AT))))).toBeTruthy();
});

it("falls back to created_at for a run that never started", () => {
  const created = "2026-08-08T11:22:33.190839";
  render(
    <ArtifactView
      tab="errors"
      ticket={makeTicket()}
      runs={[{ ...RUNS[0], started_at: null, finished_at: null, created_at: created }]}
    />,
  );

  expect(screen.getByText(new RegExp(escapeRegExp(formatLocalTimestamp(created))))).toBeTruthy();
});

it("renders no time row when a run carries no timestamps at all", () => {
  render(
    <ArtifactView
      tab="errors"
      ticket={makeTicket()}
      runs={[{ id: "r", run_code: "run_bare", status: "failed", command: "codex exec" }]}
    />,
  );

  expect(screen.getByText("run_bare")).toBeTruthy();
  // No time row at all — not a placeholder, and never "Invalid Date".
  expect(screen.queryByText(/Invalid Date/)).toBeNull();
  expect(screen.queryByText(/\b20\d{2}\b/)).toBeNull();
});

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
