import { screen, waitFor, within } from "@testing-library/react";

import type { OperatorQuestionRow, OperatorQuestionsFeed } from "../../api/operatorQuestionApi";
import { renderWithRouter } from "../../test/renderWithRouter";
import { TicketAssumptions } from "../TicketAssumptions";

const ticketOperatorQuestions = jest.fn();

jest.mock("../../api/client", () => ({
  api: { ticketOperatorQuestions: (id: string) => ticketOperatorQuestions(id) },
}));

function row(overrides: Partial<OperatorQuestionRow> = {}): OperatorQuestionRow {
  return {
    id: "q1",
    stage_key: "implement",
    question: "Cache per workspace?",
    options: ["Yes", "No"],
    default: "Yes",
    confidence: "medium",
    reversibility: "easy",
    affects: "implement",
    context: "",
    status: "approved",
    outcome: "confirmed",
    answer: "Yes",
    holds_gate: false,
    created_at: "2026-11-17T10:00:00Z",
    resolved_at: "2026-11-17T10:05:00Z",
    ...overrides,
  };
}

function feed(questions: OperatorQuestionRow[]): OperatorQuestionsFeed {
  const open = questions.filter((q) => q.status === "pending").length;
  return { ticket: "lg-x-1", open, holding_gate: open, total: questions.length, questions };
}

describe("TicketAssumptions", () => {
  beforeEach(() => ticketOperatorQuestions.mockReset());

  it("lists each settled question with the agent's default and the operator's answer", async () => {
    ticketOperatorQuestions.mockResolvedValue(
      feed([
        row(),
        row({ id: "q2", question: "Retry on 429?", default: "No", outcome: "overridden", answer: "Yes, 3 times" }),
        row({ id: "q3", question: "Drop old rows?", status: "ignored", outcome: "", answer: "" }),
        row({ id: "q4", question: "Still open?", status: "pending", outcome: "", answer: "" }),
      ]),
    );

    renderWithRouter(<TicketAssumptions ticketId="t1" />);

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[2].textContent)).toEqual([
      "Confirmed",
      "Overridden: Yes, 3 times",
      "Ignored — default stood",
    ]);
    expect(within(table).queryByText("Still open?")).not.toBeInTheDocument();
    expect(screen.getByText(/1 more waiting on you/)).toBeInTheDocument();
  });

  it("renders nothing when no question has been settled", async () => {
    ticketOperatorQuestions.mockResolvedValue(feed([row({ status: "pending", outcome: "" })]));

    const { container } = renderWithRouter(<TicketAssumptions ticketId="t1" />);

    await waitFor(() => expect(ticketOperatorQuestions).toHaveBeenCalledWith("t1"));
    expect(container).toBeEmptyDOMElement();
  });

  it("says when the questions could not be loaded", async () => {
    ticketOperatorQuestions.mockRejectedValue(new Error("boom"));

    renderWithRouter(<TicketAssumptions ticketId="t1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load this ticket’s questions");
  });
});
