/**
 * The fan-out review surface.
 *
 * What matters here is that all attempts are on screen at once with their own
 * diffs, that exactly one decision can be taken, and that a settled group
 * stops offering decisions — a second promote would 409 and confuse everyone.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StageFanoutReview } from "../StageFanoutReview";
import { stageFanoutApi, type FanoutGroup } from "../../lib/stageFanoutApi";

jest.mock("../../lib/stageFanoutApi", () => ({
  stageFanoutApi: { promote: jest.fn(), decline: jest.fn() },
}));

const mockPromote = stageFanoutApi.promote as jest.MockedFunction<typeof stageFanoutApi.promote>;
const mockDecline = stageFanoutApi.decline as jest.MockedFunction<typeof stageFanoutApi.decline>;

function attempt(index: number, overrides: Record<string, unknown> = {}) {
  return {
    id: `attempt-${index}`,
    attempt_index: index,
    attempt_name: `Attempt ${index + 1}`,
    agent_run_id: `run-${index}`,
    worktree_id: `wt-${index}`,
    branch: `loregarden/lg-1-attempt-${index + 1}`,
    status: "succeeded",
    started_at: null,
    finished_at: null,
    failure_details: "",
    ...overrides,
  };
}

function group(overrides: Partial<FanoutGroup> = {}): FanoutGroup {
  return {
    id: "group-1",
    ticket_id: "ticket-1",
    stage_key: "implement",
    attempt_count: 2,
    status: "open",
    outcome: "pending",
    winner_attempt_id: null,
    declined_reason: "",
    failure_summary: "",
    attempts: [attempt(0), attempt(1)],
    diffs: [
      {
        attempt_id: "attempt-0",
        branch: "loregarden/lg-1-attempt-1",
        stat: " a.py | 2 +-",
        patch: "--- a/a.py\n+++ b/a.py\n+the first answer",
        files_changed: 1,
        truncated: false,
      },
      {
        attempt_id: "attempt-1",
        branch: "loregarden/lg-1-attempt-2",
        stat: " b.py | 9 +++",
        patch: "--- a/b.py\n+++ b/b.py\n+the second answer",
        files_changed: 3,
        truncated: false,
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

test("every attempt is on screen with its own diff", () => {
  render(<StageFanoutReview group={group()} onSettled={() => {}} />);

  expect(screen.getByTestId("fanout-attempt-0")).toHaveTextContent("the first answer");
  expect(screen.getByTestId("fanout-attempt-1")).toHaveTextContent("the second answer");
  expect(screen.getByTestId("fanout-attempt-1")).toHaveTextContent("3 file(s)");
});

test("promoting one names that attempt", async () => {
  const settled = group({ outcome: "promoted", winner_attempt_id: "attempt-1" });
  mockPromote.mockResolvedValue(settled);
  const onSettled = jest.fn();

  render(<StageFanoutReview group={group()} onSettled={onSettled} />);
  fireEvent.click(screen.getAllByRole("button", { name: "Promote this one" })[1]);

  await waitFor(() => expect(onSettled).toHaveBeenCalledWith(settled));
  expect(mockPromote).toHaveBeenCalledWith("ticket-1", "group-1", "attempt-1");
});

test("keeping none declines the whole group", async () => {
  const settled = group({ outcome: "declined" });
  mockDecline.mockResolvedValue(settled);
  const onSettled = jest.fn();

  render(<StageFanoutReview group={group()} onSettled={onSettled} />);
  fireEvent.click(screen.getByRole("button", { name: "Keep none" }));

  await waitFor(() => expect(onSettled).toHaveBeenCalledWith(settled));
  expect(mockDecline).toHaveBeenCalledWith("ticket-1", "group-1");
});

test("a settled group offers no further decisions", () => {
  render(
    <StageFanoutReview
      group={group({ outcome: "promoted", winner_attempt_id: "attempt-0" })}
      onSettled={() => {}}
    />,
  );

  expect(screen.queryByRole("button", { name: "Promote this one" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Keep none" })).not.toBeInTheDocument();
  expect(screen.getByTestId("fanout-attempt-0")).toHaveTextContent("promoted");
});

test("a failed attempt says why, and cannot be promoted", () => {
  const failed = group({
    attempts: [
      attempt(0),
      attempt(1, { status: "failed", branch: "", failure_details: "agent exited with no report" }),
    ],
  });

  render(<StageFanoutReview group={failed} onSettled={() => {}} />);

  expect(screen.getByTestId("fanout-attempt-1")).toHaveTextContent("agent exited with no report");
  const buttons = screen.getAllByRole("button", { name: "Promote this one" });
  expect(buttons[1]).toBeDisabled();
});
