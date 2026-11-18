import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { Approval } from "../../../../api/client";
import type { OperatorQuestionRow, OperatorQuestionsFeed } from "../../../../api/operatorQuestionApi";
import { OperatorQuestionPrimitive } from "../OperatorQuestionPrimitive";
import type { OperatorQuestionPart } from "../types";

const ticketOperatorQuestions = jest.fn();
const approvals = jest.fn();
const resolveApproval = jest.fn();

jest.mock("../../../../api/client", () => ({
  api: {
    ticketOperatorQuestions: (id: string) => ticketOperatorQuestions(id),
    approvals: (id: string) => approvals(id),
    resolveApproval: (id: string, body: unknown) => resolveApproval(id, body),
  },
}));

const QUESTION = "Should the cache key include the workspace slug?";

const APPROVAL: Approval = {
  id: "appr_q",
  title: "Assumed — cache key",
  level: "medium",
  workspace_slug: "loregarden",
  stage_key: "implement",
  stage_name: "Implement",
  impact: "Proceeding with 'Yes, per workspace'.",
  checklist: [],
  route_options: [],
  ticket_id: "ticket_1",
  ticket_external_id: "lg-x-1",
  kind: "operator_question",
  status: "pending",
  run_id: "",
  tool_name: "",
  tool_input_json: JSON.stringify({
    questions: [
      {
        question: QUESTION,
        header: "Assumption",
        multiSelect: false,
        options: [{ label: "Yes, per workspace" }, { label: "No, global" }],
      },
    ],
    operator_question: {
      default: "Yes, per workspace",
      confidence: "medium",
      reversibility: "easy",
      affects: "implement",
      asked_at_stage: "implement",
      context: "",
    },
  }),
  cli_adapter: "",
};

function row(overrides: Partial<OperatorQuestionRow> = {}): OperatorQuestionRow {
  return {
    id: "appr_q",
    stage_key: "implement",
    question: QUESTION,
    options: ["Yes, per workspace", "No, global"],
    default: "Yes, per workspace",
    confidence: "medium",
    reversibility: "easy",
    affects: "implement",
    context: "",
    status: "pending",
    outcome: "",
    answer: "",
    holds_gate: true,
    created_at: "2026-11-18T10:00:00Z",
    resolved_at: null,
    ...overrides,
  };
}

function feed(questions: OperatorQuestionRow[]): OperatorQuestionsFeed {
  const open = questions.filter((q) => q.status === "pending").length;
  return { ticket: "lg-x-1", open, holding_gate: open, total: questions.length, questions };
}

function renderPart(part: Partial<OperatorQuestionPart> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OperatorQuestionPrimitive
          part={{ primitive: "operator_question", approval_id: "appr_q", ticket_id: "ticket_1", ...part }}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("OperatorQuestionPrimitive", () => {
  beforeEach(() => {
    ticketOperatorQuestions.mockReset().mockResolvedValue(feed([row()]));
    approvals.mockReset().mockResolvedValue([APPROVAL]);
    resolveApproval.mockReset().mockResolvedValue({});
  });

  it("stages the operator's chat answer and records it only on their press", async () => {
    renderPart({ answer: "No, global" });

    const send = await screen.findByRole("button", { name: "Override & send back" });
    expect(screen.getByRole("radio", { name: /No, global/ })).toBeChecked();
    expect(resolveApproval).not.toHaveBeenCalled();

    fireEvent.click(send);

    await waitFor(() =>
      expect(resolveApproval).toHaveBeenCalledWith(
        "appr_q",
        expect.objectContaining({ action: "approve", answers: { [QUESTION]: "No, global" } }),
      ),
    );
  });

  it("stages a staged default as a plain confirm", async () => {
    renderPart({ answer: "yes, per workspace" });

    expect(await screen.findByRole("button", { name: "Confirm" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Yes, per workspace/ })).toBeChecked();
  });

  it("puts an answer that matches no option in the typed answer", async () => {
    renderPart({ answer: "Per workspace, but share the global warm-up" });

    expect(await screen.findByRole("button", { name: "Override & send back" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Custom answer/ })).toHaveValue(
      "Per workspace, but share the global warm-up",
    );
  });

  it("says what was decided once the question is settled", async () => {
    ticketOperatorQuestions.mockResolvedValue(
      feed([row({ status: "approved", outcome: "overridden", answer: "No, global" })]),
    );
    approvals.mockResolvedValue([]);
    renderPart({ answer: "No, global" });

    expect(await screen.findByText("Overridden: No, global. implement re-runs with it.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Confirm|Override/ })).not.toBeInTheDocument();
  });

  it("names a question it cannot find instead of drawing an empty card", async () => {
    ticketOperatorQuestions.mockResolvedValue(feed([]));
    renderPart({ approval_id: "appr_gone" });

    expect(await screen.findByText(/No question appr_gone on ticket_1/)).toBeInTheDocument();
  });
});
