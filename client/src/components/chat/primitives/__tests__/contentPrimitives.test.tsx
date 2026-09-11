/**
 * Primitives that render fetched content — a workspace summary, a todo list, git
 * history, a Q&A exchange, a Giphy embed.
 *
 * Split out of `primitives.test.tsx` when that file passed the 1200-line cap.
 * The seam is what the tests are *about*: these six exercise a primitive reading
 * data and drawing it, while the file they came from covers primitive chrome,
 * framing, and the ticket/gate/workflow family. The `jest.mock` calls are
 * per-file by necessity — jest hoists them — and are trimmed here to the three
 * functions these tests actually drive.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import { api } from "../../../../api/client";
import { fetchBranchActivity, fetchCommitSnapshot } from "../../../../lib/branchTriageApi";
import { GiphyPrimitive } from "../GiphyPrimitive";
import { BranchHistoryPrimitive, CommitPrimitive } from "../GitPrimitive";
import { QAPrimitive } from "../QAPrimitive";
import { TodoListPrimitive } from "../TodoListPrimitive";
import { WorkspacePrimitive } from "../WorkspacePrimitive";
import type { QAItem, TodoItem } from "../types";

jest.mock("../../../../lib/branchTriageApi", () => ({
  fetchBranchActivity: jest.fn(),
  fetchCommitSnapshot: jest.fn(),
}));

jest.mock("../../../../api/client", () => {
  const actual = jest.requireActual("../../../../api/client");
  return { ...actual, api: { ...actual.api, workspaces: jest.fn() } };
});

const mockedApi = api as jest.Mocked<typeof api>;
const mockedBranchActivity = fetchBranchActivity as jest.MockedFunction<
  typeof fetchBranchActivity
>;
const mockedCommitSnapshot = fetchCommitSnapshot as jest.MockedFunction<
  typeof fetchCommitSnapshot
>;

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("workspace, todo, git, Q&A, and Giphy primitives", () => {
  it("loads a live workspace summary", async () => {
    mockedApi.workspaces.mockResolvedValue([
      {
        id: "ws-1",
        slug: "loregarden",
        name: "Lore Garden",
        repo_path: "/workspace/loregarden",
        repo_root: "/workspace/loregarden",
        repo_exists: true,
        ticket_count: 12,
        blocked_count: 2,
        workflow_template_slug: "tdd",
        cli_adapter: "cursor",
        claude_model: "",
        cursor_model: "gpt-5",
        lmstudio_base_url: "",
        lmstudio_model: "",
      },
    ]);

    wrap(
      <WorkspacePrimitive
        part={{ primitive: "workspace", workspace_slug: "loregarden" }}
      />,
    );

    expect(await screen.findByText("Lore Garden")).toBeInTheDocument();
    expect(screen.getByText("12 tickets")).toBeInTheDocument();
    expect(screen.getByText("2 blocked")).toBeInTheDocument();
    expect(screen.getByText("tdd")).toBeInTheDocument();
  });

  it("keeps agent todos read-only and lets users toggle the same component", () => {
    const agent = render(
      <TodoListPrimitive
        part={{
          primitive: "todo_list",
          owner: "agent",
          items: [{ id: "test", text: "Run tests", checked: false }],
        }}
      />,
    );
    expect(screen.getByRole("checkbox", { name: "Run tests" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    agent.unmount();

    render(
      <TodoListPrimitive
        part={{
          primitive: "todo_list",
          owner: "user",
          items: [{ id: "review", text: "Review diff", checked: false }],
        }}
      />,
    );
    const checkbox = screen.getByRole("checkbox", { name: "Review diff" });
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    expect(screen.getByText("1/1 complete")).toBeInTheDocument();
  });

  it("runs an agent execution plan through the chat callback", () => {
    const onSubmit = jest.fn();
    render(
      <TodoListPrimitive
        part={{
          primitive: "todo_list",
          owner: "agent",
          title: "Agent execution plan",
          items: [
            { id: "api", text: "Add history API", checked: false },
            { id: "verify", text: "Run focused tests", checked: true },
          ],
        }}
        onSubmit={onSubmit}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenCalledWith(
      [
        "Execute this agent execution plan now. Complete each unchecked step using tools.",
        "As you finish steps, re-emit the same todo_list with checked:true on completed items.",
        "",
        "Plan: Agent execution plan",
        "- [ ] Add history API (id: api)",
        "- [x] Run focused tests (id: verify)",
      ].join("\n"),
    );
    expect(screen.getByText("Execution requested")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });

  it("hides Run when every agent plan step is already checked", () => {
    render(
      <TodoListPrimitive
        part={{
          primitive: "todo_list",
          owner: "agent",
          title: "Agent execution plan",
          items: [{ id: "done", text: "Ship it", checked: true }],
        }}
        onSubmit={jest.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    expect(screen.getByText("All steps complete")).toBeInTheDocument();
  });

  it("keeps user ticks through a re-render but adopts a changed payload", () => {
    const userList = (items: TodoItem[]) => (
      <TodoListPrimitive part={{ primitive: "todo_list", owner: "user", items }} />
    );
    const original: TodoItem[] = [{ id: "review", text: "Review diff", checked: false }];
    const view = render(userList(original));

    fireEvent.click(screen.getByRole("checkbox", { name: "Review diff" }));
    // A fresh array with identical contents stands in for an unrelated
    // re-render of the thread; the tick must survive it.
    view.rerender(userList([{ ...original[0] }]));
    expect(screen.getByRole("checkbox", { name: "Review diff" })).toBeChecked();

    view.rerender(userList([{ id: "ship", text: "Approve release", checked: false }]));
    expect(screen.getByRole("checkbox", { name: "Approve release" })).not.toBeChecked();
    expect(screen.getByText("0/1 complete")).toBeInTheDocument();
  });

  it("keeps typed Q&A answers through a re-render", () => {
    const onSubmit = jest.fn();
    const card = (items: QAItem[]) => (
      <QAPrimitive part={{ primitive: "qa", items }} onSubmit={onSubmit} />
    );
    const items: QAItem[] = [{ id: "scope", question: "Who is this for?" }];
    const view = render(card(items));

    fireEvent.change(screen.getByLabelText("Who is this for?"), {
      target: { value: "Operators" },
    });
    view.rerender(card([{ ...items[0] }]));

    expect(screen.getByLabelText("Who is this for?")).toHaveValue("Operators");
    expect(screen.getByRole("button", { name: "Send answers" })).toBeEnabled();
  });

  it("does not offer to answer a Q&A card that carries no questions", () => {
    // The empty card used to read "Answer these before continuing" over a Send
    // button nothing could enable, and it halted plan execution waiting for an
    // answer to nothing.
    render(<QAPrimitive part={{ primitive: "qa", items: [] }} onSubmit={jest.fn()} />);

    expect(screen.queryByRole("button", { name: "Send answers" })).toBeNull();
    expect(screen.getByText(/carried no questions/)).toBeInTheDocument();
    expect(screen.queryByText("Answer these before continuing")).toBeNull();
  });

  it("counts a single question in the singular", () => {
    render(
      <QAPrimitive
        part={{ primitive: "qa", items: [{ id: "scope", question: "Who is this for?" }] }}
        onSubmit={jest.fn()}
      />,
    );

    expect(screen.getByText("1 question")).toBeInTheDocument();
  });

  it("renders live branch history and commit detail", async () => {
    mockedBranchActivity.mockResolvedValue({
      branch: "main",
      upstream: "origin/main",
      commits: [
        {
          sha: "a".repeat(40),
          short_sha: "aaaaaaa",
          date: new Date().toISOString(),
          author: "Baxter",
          message: "Add primitives",
          pushed: true,
        },
      ],
    });
    mockedCommitSnapshot.mockResolvedValue({
      sha: "a".repeat(40),
      short_sha: "aaaaaaa",
      date: new Date().toISOString(),
      author: "Baxter",
      message: "Add primitives",
      body: "Implement the new cards.",
      pushed: false,
      files_changed: 3,
      insertions: 42,
      deletions: 5,
    });

    const branch = wrap(
      <BranchHistoryPrimitive
        part={{
          primitive: "branch_history",
          workspace_slug: "loregarden",
          branch: "main",
        }}
      />,
    );
    expect(await screen.findByText("Add primitives")).toBeInTheDocument();
    expect(screen.getByText("tracking origin/main")).toBeInTheDocument();
    branch.unmount();

    wrap(
      <CommitPrimitive
        part={{
          primitive: "commit",
          workspace_slug: "loregarden",
          sha: "aaaaaaa",
          branch: "main",
        }}
      />,
    );
    expect(await screen.findByText("Implement the new cards.")).toBeInTheDocument();
    expect(screen.getByText("3 files")).toBeInTheDocument();
    expect(screen.getByText("+42")).toBeInTheDocument();
    expect(screen.getByText("−5")).toBeInTheDocument();
  });

  it("sends complete Q&A responses through the chat callback", () => {
    const onSubmit = jest.fn();
    render(
      <QAPrimitive
        part={{
          primitive: "qa",
          items: [{ id: "scope", question: "Who is this for?" }],
        }}
        onSubmit={onSubmit}
      />,
    );

    const send = screen.getByRole("button", { name: "Send answers" });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Who is this for?"), {
      target: { value: "Operators" },
    });
    fireEvent.click(send);

    expect(onSubmit).toHaveBeenCalledWith(
      "1. Who is this for?\nAnswer: Operators",
    );
    expect(screen.getByText("Answers sent")).toBeInTheDocument();
  });

  it("renders only allowlisted Giphy media URLs", () => {
    const view = render(
      <GiphyPrimitive
        part={{
          primitive: "giphy",
          giphy_id: "ICOgUNjpvO0PC",
          alt: "Typing cat",
        }}
      />,
    );
    const image = screen.getByRole("img", { name: "Typing cat" });
    expect(image).toHaveAttribute(
      "src",
      "https://media.giphy.com/media/ICOgUNjpvO0PC/giphy.gif",
    );
    fireEvent.error(image);
    expect(screen.getByText("This Giphy image could not be loaded")).toBeInTheDocument();

    view.rerender(
      <GiphyPrimitive
        part={{
          primitive: "giphy",
          url: "https://example.com/tracker.gif",
        }}
      />,
    );
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText(/valid Giphy ID/)).toBeInTheDocument();
  });
});
