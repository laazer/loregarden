/**
 * The fan-out review surface.
 *
 * What matters here is that all attempts are on screen at once with their own
 * numbers, that a patch is only fetched when someone opens a file, that
 * exactly one decision can be taken, and that nothing can be promoted while an
 * attempt is still running or after the group has settled.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StageFanoutReview } from "../StageFanoutReview";
import { stageFanoutApi, type FanoutGroup } from "../../lib/stageFanoutApi";

jest.mock("../../lib/stageFanoutApi", () => ({
  stageFanoutApi: { promote: jest.fn(), decline: jest.fn(), fileDiff: jest.fn() },
}));

const mockPromote = stageFanoutApi.promote as jest.MockedFunction<typeof stageFanoutApi.promote>;
const mockDecline = stageFanoutApi.decline as jest.MockedFunction<typeof stageFanoutApi.decline>;
const mockFileDiff = stageFanoutApi.fileDiff as jest.MockedFunction<typeof stageFanoutApi.fileDiff>;

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
        files: [{ path: "a.py", additions: 2, deletions: 1 }],
        files_changed: 1,
        additions: 2,
        deletions: 1,
      },
      {
        attempt_id: "attempt-1",
        branch: "loregarden/lg-1-attempt-2",
        files: [
          { path: "a.py", additions: 9, deletions: 0 },
          { path: "b.py", additions: 4, deletions: 2 },
        ],
        files_changed: 2,
        additions: 13,
        deletions: 2,
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

test("every attempt is on screen with its own totals", () => {
  render(<StageFanoutReview group={group()} onSettled={() => {}} />);

  expect(screen.getByTestId("fanout-attempt-0")).toHaveTextContent("1 file(s)");
  const second = screen.getByTestId("fanout-attempt-1");
  expect(second).toHaveTextContent("2 file(s)");
  expect(second).toHaveTextContent("+13");
  expect(second).toHaveTextContent("−2");
});

test("a patch is fetched only when its file is opened", async () => {
  mockFileDiff.mockResolvedValue({
    attempt_id: "attempt-1",
    path: "b.py",
    patch: "+++ the second answer",
    truncated: false,
  });

  render(<StageFanoutReview group={group()} onSettled={() => {}} />);
  expect(mockFileDiff).not.toHaveBeenCalled();

  fireEvent.click(within(screen.getByTestId("fanout-attempt-1")).getByText("b.py"));

  await waitFor(() =>
    expect(screen.getByTestId("patch-1")).toHaveTextContent("the second answer"),
  );
  expect(mockFileDiff).toHaveBeenCalledWith("ticket-1", "group-1", "attempt-1", "b.py");
  expect(mockFileDiff).toHaveBeenCalledTimes(1);
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

test("nothing can be settled while an attempt is still running", () => {
  const running = group({ attempts: [attempt(0), attempt(1, { status: "running" })] });

  render(<StageFanoutReview group={running} onSettled={() => {}} />);

  expect(screen.getByRole("button", { name: "Keep none" })).toBeDisabled();
  const buttons = screen.getAllByRole("button", { name: "Promote this one" });
  expect(buttons[1]).toBeDisabled();
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
  expect(screen.getAllByRole("button", { name: "Promote this one" })[1]).toBeDisabled();
});

test("an error from the settle call is shown, not swallowed", async () => {
  mockPromote.mockRejectedValue(new Error("merge conflict in a.py"));

  render(<StageFanoutReview group={group()} onSettled={() => {}} />);
  fireEvent.click(screen.getAllByRole("button", { name: "Promote this one" })[0]);

  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("merge conflict in a.py"));
});
