import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import type { ChatFocus, FeatureLoop } from "../../../api/chatWorkbenchTypes";
import { ApiError, api } from "../../../api/client";
import { ChangesCard } from "../workbench/ChangesCard";
import { FeatureCard } from "../workbench/FeatureCard";
import { ScopeCard } from "../workbench/ScopeCard";

jest.mock("../../../api/client", () => {
  const actual = jest.requireActual("../../../api/client");
  return {
    ...actual,
    api: { ...actual.api, workspaces: jest.fn(), initiatives: jest.fn(), tickets: jest.fn() },
  };
});

const mockedApi = api as jest.Mocked<typeof api>;

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const FOCUS: ChatFocus = {
  all_workspaces: false,
  workspaces: [
    { id: "w1", slug: "loregarden", fixed: true },
    { id: "w2", slug: "blobert", fixed: false },
  ],
  initiatives: [{ id: "i1", external_id: "init-1", title: "Cross-repo launch", fixed: false }],
};

function loop(overrides: Partial<FeatureLoop> = {}): FeatureLoop {
  return {
    ticket: {
      id: "t1",
      external_id: "lg-dark-1",
      title: "Dark mode",
      state: "in_progress",
      stage_key: "implement",
      workspace_slug: "loregarden",
      criteria_count: 1,
    },
    status: "running",
    reason: "",
    round: 1,
    max_criteria: 12,
    criteria: [
      { id: "c1", text: "Toggle persists", status: "in_round", round: 1 },
      { id: "c2", text: "Respects the OS setting", status: "proposed", round: 0 },
      { id: "c3", text: "Contrast passes AA", status: "proposed", round: 0 },
    ],
    runs: [],
    ...overrides,
  };
}

beforeEach(() => {
  mockedApi.workspaces.mockResolvedValue([
    { slug: "loregarden", archived_at: null },
    { slug: "blobert", archived_at: null },
    { slug: "lore-eden", archived_at: null },
  ] as never);
  mockedApi.initiatives.mockResolvedValue([
    { id: "i1", external_id: "init-1", title: "Cross-repo launch", state: "in_progress" },
    { id: "i2", external_id: "init-2", title: "Second push", state: "backlog" },
  ] as never);
  mockedApi.tickets.mockResolvedValue([
    { id: "m1", external_id: "lg-ms-9", title: "Q4 polish" },
  ] as never);
});

describe("ScopeCard", () => {
  it("names every workspace and initiative in scope, and where turns run", () => {
    wrap(<ScopeCard focus={FOCUS} isGlobal={false} hostSlug="loregarden" saving={false} onSave={jest.fn()} />);

    const workspaces = screen.getByRole("list", { name: "Workspaces in scope" });
    expect(within(workspaces).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "▤loregardenruns here",
      "▤blobert",
    ]);
    expect(screen.getByRole("button", { name: "Cross-repo launch" })).toBeInTheDocument();
  });

  it("edits the focus in a modal: several of each, saved as one replacement", async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    wrap(<ScopeCard focus={FOCUS} isGlobal={false} hostSlug="loregarden" saving={false} onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: /lore-eden/ }));
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: /Second push/ }));
    expect(within(dialog).getByRole("checkbox", { name: /loregarden/ })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Focus on 5" }));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({
        workspace_slugs: ["blobert", "lore-eden"],
        initiative_ids: ["i1", "i2"],
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("keeps the dialog and says why when the focus is refused", async () => {
    const onSave = jest.fn().mockRejectedValue(new ApiError(400, "No workspace named ghost"));
    wrap(<ScopeCard focus={FOCUS} isGlobal={false} hostSlug="loregarden" saving={false} onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Focus on 3" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("No workspace named ghost");
  });
});

describe("FeatureCard", () => {
  const handlers = () => ({
    onStart: jest.fn().mockResolvedValue(undefined),
    onDecide: jest.fn(),
    onAdd: jest.fn().mockResolvedValue(undefined),
  });

  it("without a loop, starts one in a modal under a picked milestone", async () => {
    const h = handlers();
    wrap(
      <FeatureCard
        feature={null}
        workspaces={["loregarden", "blobert"]}
        defaultWorkspace="loregarden"
        busy={false}
        starting={false}
        {...h}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Start a feature loop…" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Dark mode" } });
    await within(dialog).findByRole("option", { name: "lg-ms-9 · Q4 polish" });
    fireEvent.change(within(dialog).getByLabelText("Under milestone"), { target: { value: "m1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create and start loop" }));

    await waitFor(() =>
      expect(h.onStart).toHaveBeenCalledWith({
        title: "Dark mode",
        workspace_slug: "loregarden",
        parent_ticket_id: "m1",
      }),
    );
  });

  it("puts proposals waiting on the operator first, each with Accept and Drop", () => {
    const h = handlers();
    wrap(<FeatureCard feature={loop()} workspaces={[]} defaultWorkspace="" busy={false} starting={false} {...h} />);

    const groups = screen.getAllByRole("heading", { level: 4 }).map((el) => el.textContent);
    expect(groups).toEqual(["Proposed — waiting on you 2", "In this round 1"]);
    fireEvent.click(screen.getByRole("button", { name: "Accept: Respects the OS setting" }));
    fireEvent.click(screen.getByRole("button", { name: "Drop: Contrast passes AA" }));
    fireEvent.click(screen.getByRole("button", { name: "Accept all" }));

    expect(h.onDecide.mock.calls).toEqual([
      [["c2"], true],
      [["c3"], false],
      [["c2", "c3"], true],
    ]);
  });

  it("queues a criterion the operator types, and clears the field once it lands", async () => {
    const h = handlers();
    wrap(<FeatureCard feature={loop()} workspaces={[]} defaultWorkspace="" busy={false} starting={false} {...h} />);

    const field = screen.getByRole("textbox", { name: "Add an acceptance criterion" });
    fireEvent.change(field, { target: { value: "Works offline" } });
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));

    expect(h.onAdd).toHaveBeenCalledWith("Works offline");
    await waitFor(() => expect(field).toHaveValue(""));
  });

  it("disables every decision while one is in flight", () => {
    wrap(<FeatureCard feature={loop()} workspaces={[]} defaultWorkspace="" busy starting={false} {...handlers()} />);

    expect(screen.getByRole("button", { name: "Accept: Respects the OS setting" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Accept all" })).toBeDisabled();
  });

  it("says why a stalled loop is not moving", () => {
    wrap(
      <FeatureCard
        feature={loop({ status: "blocked", reason: "Gate failed: lint" })}
        workspaces={[]}
        defaultWorkspace=""
        busy={false}
        starting={false}
        {...handlers()}
      />,
    );

    expect(screen.getByText("round 1 · stalled")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Gate failed: lint");
  });
});

describe("ChangesCard", () => {
  it("shows the chat's branch and the loop's runs, the live ones counted", () => {
    wrap(
      <ChangesCard
        branch={{ branch: "chat/dark-mode", parent_branch: "main", worktree_path: "/w", has_conflicts: false }}
        runs={[
          { id: "r1", run_code: "run_1", agent_id: "implementer", stage_key: "implement", status: "running", started_at: null, finished_at: null },
          { id: "r2", run_code: "run_0", agent_id: "planner", stage_key: "plan", status: "succeeded", started_at: null, finished_at: null },
        ]}
        hasLoop
      />,
    );

    expect(screen.getByText("chat/dark-mode")).toBeInTheDocument();
    expect(screen.getByText("1 running")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Feature runs" })).getAllByRole("listitem")).toHaveLength(2);
  });

  it("says what will appear before anything has run", () => {
    wrap(<ChangesCard branch={null} runs={[]} hasLoop />);

    expect(screen.getByText(/No branch yet/)).toBeInTheDocument();
    expect(screen.getByText(/A round starts when you accept criteria/)).toBeInTheDocument();
  });
});
