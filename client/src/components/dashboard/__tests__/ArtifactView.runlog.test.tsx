import { fireEvent, render, screen } from "@testing-library/react";

import { ArtifactView } from "../ArtifactView";
import type { TicketDetail } from "../../../api/client";

const RUNS = [
  { id: "run-id-1", run_code: "run_aaa", status: "succeeded", command: "claude -p plan", agent_id: "planner", stage_key: "plan" },
  { id: "run-id-2", run_code: "run_bbb", status: "failed", command: "claude -p test", agent_id: "static_qa", stage_key: "testing", stderr: "boom" },
];

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t1",
    stages: [{ key: "plan", name: "Plan", status: "done", agent_id: "planner" }],
    blocking_issues: "",
    artifacts: {},
    ...overrides,
  } as unknown as TicketDetail;
}

it("opens the log for a run clicked in the context tab", () => {
  const onOpenRunLog = jest.fn();
  render(<ArtifactView tab="context" ticket={makeTicket()} runs={RUNS} onOpenRunLog={onOpenRunLog} />);

  fireEvent.click(screen.getByRole("button", { name: /run_aaa/ }));

  expect(onOpenRunLog).toHaveBeenCalledWith("run-id-1");
});

it("opens the log for a failed run in the errors tab", () => {
  const onOpenRunLog = jest.fn();
  render(<ArtifactView tab="errors" ticket={makeTicket()} runs={RUNS} onOpenRunLog={onOpenRunLog} />);

  fireEvent.click(screen.getByRole("button", { name: /view log/i }));

  expect(onOpenRunLog).toHaveBeenCalledWith("run-id-2");
});

it("resolves the error artifact's run_code to a run id", () => {
  const onOpenRunLog = jest.fn();
  const ticket = makeTicket({
    artifacts: {
      error: {
        message: "it failed",
        run_code: "run_bbb",
        agent_id: "static_qa",
        stage_key: "testing",
        command: "claude -p test",
      },
    },
  } as Partial<TicketDetail>);

  render(<ArtifactView tab="errors" ticket={ticket} runs={RUNS} onOpenRunLog={onOpenRunLog} />);
  // Both the error-artifact card and the failed-run card offer a log button.
  fireEvent.click(screen.getAllByRole("button", { name: /view log/i })[0]);

  expect(onOpenRunLog).toHaveBeenCalledWith("run-id-2");
});

it("offers no log affordance when the handler is absent", () => {
  render(<ArtifactView tab="errors" ticket={makeTicket()} runs={RUNS} />);

  expect(screen.queryByRole("button", { name: /view log/i })).not.toBeInTheDocument();
});
