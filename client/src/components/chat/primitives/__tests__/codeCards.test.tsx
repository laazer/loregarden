import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { fetchBranchActivity, fetchCommitSnapshot } from "../../../../lib/branchTriageApi";
import { BranchHistoryPrimitive, CommitPrimitive } from "../GitPrimitive";
import { QAPrimitive } from "../QAPrimitive";
import { TerminalPrimitive } from "../TerminalPrimitive";
import { TodoListPrimitive } from "../TodoListPrimitive";

jest.mock("../../../../lib/branchTriageApi", () => ({
  fetchBranchActivity: jest.fn(),
  fetchCommitSnapshot: jest.fn(),
}));

const mockActivity = fetchBranchActivity as jest.MockedFunction<typeof fetchBranchActivity>;
const mockCommit = fetchCommitSnapshot as jest.MockedFunction<typeof fetchCommitSnapshot>;

const DAY_AGO = new Date(Date.now() - 26 * 3600_000).toISOString();

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("terminal card", () => {
  it("says how the command exited as the chip, and keeps the exit line out of the output", () => {
    const { container } = render(
      <TerminalPrimitive
        part={{
          primitive: "terminal",
          title: "client · npm test",
          lines: [
            { kind: "command", text: "npm test" },
            { kind: "stdout", text: "Tests: 108 passed" },
            { kind: "status", text: "exit 0" },
          ],
        }}
      />,
    );
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(container.querySelector(".lg-primitive-card-actions")).toHaveTextContent("exit 0");
    expect(container.querySelector(".lg-primitive-terminal-body")).not.toHaveTextContent("exit 0");
  });

  it("names a failing exit code", () => {
    render(
      <TerminalPrimitive part={{ primitive: "terminal", lines: [{ kind: "status", text: "exited with code 2" }] }} />,
    );
    expect(screen.getByText("Exited 2")).toBeInTheDocument();
  });
});

describe("git cards", () => {
  it("lists commits newest first as sha, subject and age, and opens branch triage from the footer", async () => {
    mockActivity.mockResolvedValue({
      upstream: "origin/main",
      commits: [
        { sha: "a1", short_sha: "fc141083", message: "fix: stdin", author: "laazer", date: DAY_AGO, pushed: true },
        { sha: "b2", short_sha: "8d980d95", message: "wip: local", author: "laazer", date: DAY_AGO, pushed: false },
      ],
    } as never);
    const { container } = wrap(
      <BranchHistoryPrimitive part={{ primitive: "branch_history", workspace_slug: "loregarden", branch: "main" }} />,
    );

    const rows = await screen.findAllByRole("listitem");
    expect(rows[0]).toHaveTextContent(/fc141083fix: stdin1d ago/);
    expect(rows[1]).toHaveTextContent("local only");
    const footer = container.querySelector(".lg-primitive-card-actions") as HTMLElement;
    expect(footer).toHaveTextContent("2 recent commits");
    expect(within(footer).getByRole("button", { name: /branch triage/i })).toBeInTheDocument();
  });

  it("says when a commit was pushed in its chip", async () => {
    mockCommit.mockResolvedValue({
      sha: "a1",
      short_sha: "fc141083",
      message: "fix: stdin",
      author: "laazer",
      date: DAY_AGO,
      pushed: true,
      body: "",
      files_changed: 1,
      insertions: 2,
      deletions: 0,
    } as never);
    wrap(<CommitPrimitive part={{ primitive: "commit", workspace_slug: "loregarden", sha: "a1", branch: "main" }} />);

    expect(await screen.findByText("Pushed 1d ago")).toBeInTheDocument();
    expect(screen.getByText("fc141083 · laazer · main")).toBeInTheDocument();
  });
});

describe("plans and asks", () => {
  it("grows the agent's steps on a vine and says where Run picks up", () => {
    render(
      <TodoListPrimitive
        part={{
          primitive: "todo_list",
          owner: "agent",
          items: [
            { id: "a", text: "Inspect", checked: true },
            { id: "b", text: "Implement", checked: true },
            { id: "c", text: "Test", checked: false },
          ],
        }}
        onSubmit={jest.fn()}
      />,
    );
    const steps = screen.getAllByRole("listitem");
    expect(steps.map((step) => step.querySelector(".vine-node")?.getAttribute("data-mark"))).toEqual([
      "leaf",
      "leaf",
      "bud-closed",
    ]);
    expect(screen.getByText("Run continues from step 3")).toBeInTheDocument();
  });

  it("gives an open question card the one coloured edge, and drops it once answered", () => {
    const { container } = render(
      <QAPrimitive part={{ primitive: "qa", items: [{ id: "q", question: "Who is it for?" }] }} onSubmit={jest.fn()} />,
    );
    expect(container.querySelector(".lg-primitive-card")).toHaveClass("lg-primitive-card--needs-you");

    fireEvent.change(screen.getByLabelText("Who is it for?"), { target: { value: "Operators" } });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    expect(container.querySelector(".lg-primitive-card")).not.toHaveClass("lg-primitive-card--needs-you");
  });
});
