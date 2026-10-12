import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../../../api/client";
import { ApiError } from "../../../api/http";
import type { AutoFixAttempt, CIRunResult, CIStatusResponse } from "../../../api/types";
import { TicketCIAutoFix } from "../TicketCIAutoFix";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const FAILING: CIRunResult = {
  id: "r1",
  workspace_id: "w1",
  ticket_id: "t1",
  status: "failing",
  provider: "github_actions",
  head_sha: "c".repeat(40),
  pr_number: 555,
  external_run_id: null,
  logs_url: null,
  failure_summary: "1 failing check(s): server-tests",
  full_logs: null,
  created_at: "2026-10-12T10:00:00",
  updated_at: "2026-10-12T10:05:00",
};

function attempt(overrides: Partial<AutoFixAttempt>): AutoFixAttempt {
  return {
    id: "a1",
    ci_run_result_id: "r1",
    attempt_number: 1,
    run_id: null,
    status: "failed",
    result_summary: null,
    created_at: "2026-10-12T10:06:00",
    completed_at: "2026-10-12T10:30:00",
    ...overrides,
  };
}

function renderSection(data: CIStatusResponse, isOpen = true) {
  mockApi.ciStatus.mockResolvedValue(data);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TicketCIAutoFix ticketId="t1" isOpen={isOpen} />
    </QueryClientProvider>,
  );
}

describe("TicketCIAutoFix", () => {
  beforeEach(() => jest.clearAllMocks());

  it("says what will appear when no CI has been recorded for an open PR", async () => {
    renderSection({ ci_status: null, auto_fix_history: [] });

    expect(await screen.findByText(/has not recorded CI for this PR yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows nothing for a closed PR that never had an attempt", async () => {
    const { container } = renderSection({ ci_status: FAILING, auto_fix_history: [] }, false);

    await waitFor(() => expect(mockApi.ciStatus).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("lists attempts newest first, with a refusal's reason", async () => {
    renderSection({
      ci_status: FAILING,
      auto_fix_history: [
        attempt({ id: "a1", attempt_number: 1, status: "failed", result_summary: "CI failing at cccc after the fix" }),
        attempt({ id: "a2", attempt_number: 2, status: "refused", result_summary: "the pull request's head moved" }),
      ],
    });

    const rows = within(await screen.findByRole("list", { name: /fix attempts/i })).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Attempt 2: Refused");
    expect(rows[0]).toHaveTextContent("the pull request's head moved");
    expect(rows[1]).toHaveTextContent("Attempt 1: Failed");
  });

  it("does not offer a retry while an attempt is still running", async () => {
    renderSection({ ci_status: FAILING, auto_fix_history: [attempt({ status: "running", completed_at: null })] });

    expect(await screen.findByRole("button", { name: "Retry auto-fix" })).toBeDisabled();
  });

  it("shows the server's reason when a retry is refused", async () => {
    mockApi.triggerAutoFix.mockRejectedValue(
      new ApiError(409, "Auto-fix not started: the ticket is in_progress; its own workflow publishes the next commit"),
    );
    renderSection({ ci_status: FAILING, auto_fix_history: [] });

    fireEvent.click(await screen.findByRole("button", { name: "Retry auto-fix" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("the ticket is in_progress");
    expect(screen.getByRole("button", { name: "Retry auto-fix" })).toBeEnabled();
  });

  it("refetches the history after a retry starts", async () => {
    mockApi.triggerAutoFix.mockResolvedValue({ status: "ok", attempt_id: "a9", attempt_number: 1 });
    renderSection({ ci_status: FAILING, auto_fix_history: [] });

    fireEvent.click(await screen.findByRole("button", { name: "Retry auto-fix" }));

    await waitFor(() => expect(mockApi.ciStatus).toHaveBeenCalledTimes(2));
  });

  it("asks before skipping the CI gate, and Escape cancels", async () => {
    mockApi.skipCICheck.mockResolvedValue({ status: "ok", message: "" });
    renderSection({ ci_status: FAILING, auto_fix_history: [] });

    fireEvent.click(await screen.findByRole("button", { name: "Skip CI gate…" }));
    const confirm = screen.getByRole("button", { name: "Skip CI gate" });
    expect(confirm).toHaveFocus();
    fireEvent.keyDown(confirm, { key: "Escape" });
    expect(mockApi.skipCICheck).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Skip CI gate…" }));
    fireEvent.click(screen.getByRole("button", { name: "Skip CI gate" }));
    await waitFor(() => expect(mockApi.skipCICheck).toHaveBeenCalledWith("t1"));
  });

  it("names a failed read and offers to try again, rather than looking empty", async () => {
    mockApi.ciStatus.mockRejectedValueOnce(new ApiError(500, "Internal Server Error"));
    mockApi.ciStatus.mockResolvedValue({ ci_status: null, auto_fix_history: [] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <TicketCIAutoFix ticketId="t1" isOpen />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Internal Server Error");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/has not recorded CI for this PR yet/)).toBeInTheDocument();
  });
});
