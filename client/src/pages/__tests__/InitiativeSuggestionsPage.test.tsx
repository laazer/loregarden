import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api, type InitiativeSuggestionSet, type SuggestedItem } from "../../api/client";
import { ApiError } from "../../api/http";
import { navigateToInitiative, navigateToPage } from "../../lib/useAppNavigation";
import { InitiativeSuggestionsPage } from "../InitiativeSuggestionsPage";

jest.mock("../../api/client");
jest.mock("../../lib/useAppNavigation", () => ({
  ...jest.requireActual("../../lib/useAppNavigation"),
  navigateToPage: jest.fn(),
  navigateToInitiative: jest.fn(),
  navigateToTicket: jest.fn(),
}));

const mockApi = api as jest.Mocked<typeof api>;

function item(id: string, overrides: Partial<SuggestedItem> = {}): SuggestedItem {
  return {
    id,
    external_id: `lg-${id}`,
    title: `Item ${id}`,
    state: "backlog",
    workspace_slug: "loregarden",
    work_item_type: "milestone",
    from_milestone: "",
    cost: 1,
    ...overrides,
  };
}

function draft(overrides: Partial<InitiativeSuggestionSet> = {}): InitiativeSuggestionSet {
  return {
    source: "heuristic",
    suggestions: [
      {
        key: "theme:canvas",
        kind: "theme",
        title: "Canvas work",
        description: "",
        rationale: "2 open milestones mention “canvas”.",
        items: [item("m1"), item("m2")],
        empties: [],
        target_date: null,
      },
      {
        key: "sprint",
        kind: "sprint",
        title: "Sprint Oct 3 – Oct 17",
        description: "A 14-day sprint.",
        rationale: "Work already in progress first.",
        items: [
          item("f1", { work_item_type: "feature", state: "in_progress", from_milestone: "lg-m9", cost: 3 }),
          item("b1", { work_item_type: "bug", from_milestone: "lg-m9" }),
        ],
        empties: ["lg-m9"],
        target_date: "2026-10-17",
      },
    ],
    ungrouped: [item("m3")],
    sprint: { days: 14, capacity: 10, committed: 0, planned: 4, basis: "workspace_throughput" },
    warnings: [],
    generated_at: "2026-10-03T00:00:00Z",
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InitiativeSuggestionsPage />
    </QueryClientProvider>,
  );
}

const themeCard = async () => (await screen.findByDisplayValue("Canvas work")).closest("article") as HTMLElement;
const sprintCard = () => screen.getByDisplayValue("Sprint Oct 3 – Oct 17").closest("article") as HTMLElement;

beforeEach(() => {
  jest.clearAllMocks();
});

test("keyword themes start unticked and the sprint ticked", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft());
  renderPage();

  expect(within(await themeCard()).getByRole("checkbox", { name: /Create “Canvas work”/ })).not.toBeChecked();
  expect(within(sprintCard()).getByRole("checkbox", { name: "Create the sprint" })).toBeChecked();
  expect(screen.getByText("1 initiative, 2 items.")).toBeInTheDocument();
});

test("creating goes through a confirm step that says what moves, then lands on the sprint", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft());
  mockApi.applyInitiativeSuggestions.mockResolvedValue({
    created: [
      { id: "i1", external_id: "init-canvas-1", title: "Canvas", attached: 1 },
      { id: "i2", external_id: "init-sprint-2", title: "Sprint Oct 3 – Oct 17", attached: 2 },
    ],
  });
  const user = userEvent.setup();
  renderPage();

  const canvas = await themeCard();
  await user.click(within(canvas).getByRole("checkbox", { name: /Create “Canvas work”/ }));
  await user.clear(within(canvas).getByRole("textbox", { name: "Title" }));
  await user.type(within(canvas).getByRole("textbox", { name: "Title" }), "Canvas");
  await user.selectOptions(within(canvas).getByRole("combobox", { name: "Group for Item m2" }), "Not grouped");

  await user.click(screen.getByRole("button", { name: "Review and create…" }));
  const confirm = screen.getByRole("region", { name: "Create 2 initiatives?" });
  expect(confirm).toHaveTextContent("2 features and bugs move out of their milestones");
  expect(confirm).toHaveTextContent("lg-m9 will have no open work left");
  expect(mockApi.applyInitiativeSuggestions).not.toHaveBeenCalled();

  await user.click(within(confirm).getByRole("button", { name: "Create" }));
  await waitFor(() =>
    expect(mockApi.applyInitiativeSuggestions).toHaveBeenCalledWith([
      { title: "Canvas", description: "", item_ids: ["m1"], target_date: null },
      {
        title: "Sprint Oct 3 – Oct 17",
        description: "A 14-day sprint.",
        item_ids: ["f1", "b1"],
        target_date: "2026-10-17",
      },
    ]),
  );
  await waitFor(() => expect(navigateToInitiative).toHaveBeenCalledWith("init-sprint-2"));
});

test("Escape on the confirm step goes back to editing without writing", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft());
  const user = userEvent.setup();
  renderPage();

  await themeCard();
  await user.click(screen.getByRole("button", { name: "Review and create…" }));
  expect(screen.getByRole("heading", { name: "Create 1 initiative?" })).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Review and create…" })).toBeInTheDocument();
  expect(mockApi.applyInitiativeSuggestions).not.toHaveBeenCalled();
});

test("an ungrouped milestone can join a theme or start one", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft());
  const user = userEvent.setup();
  renderPage();

  const canvas = await themeCard();
  await user.selectOptions(screen.getByRole("combobox", { name: "Group for Item m3" }), "Add to Canvas work");
  expect(within(canvas).getByText("Item m3")).toBeInTheDocument();

  await user.selectOptions(within(canvas).getByRole("combobox", { name: "Group for Item m3" }), "Not grouped");
  await user.selectOptions(screen.getByRole("combobox", { name: "Group for Item m3" }), "Start a new initiative");
  expect(screen.getByRole("checkbox", { name: "Create “Item m3”" })).toBeChecked();
});

test("edits survive a redraw for another sprint length", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft());
  const user = userEvent.setup();
  renderPage();

  const canvas = await themeCard();
  await user.click(within(canvas).getByRole("checkbox", { name: /Create “Canvas work”/ }));
  await user.type(within(canvas).getByRole("textbox", { name: "Description" }), "Ship the canvas");
  await user.selectOptions(screen.getByRole("combobox", { name: "Sprint length" }), "3");
  await waitFor(() => expect(mockApi.initiativeSuggestions).toHaveBeenCalledWith(21));

  const after = await themeCard();
  expect(within(after).getByRole("textbox", { name: "Description" })).toHaveValue("Ship the canvas");
  expect(within(after).getByRole("checkbox", { name: /Create “Canvas work”/ })).toBeChecked();
});

test("the sprint trims in bulk, and the warning drops once a milestone keeps open work", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft());
  const user = userEvent.setup();
  renderPage();

  await themeCard();
  const sprint = sprintCard();
  expect(within(sprint).getByRole("status")).toHaveTextContent("1 milestone will be marked done");
  expect(within(sprint).getByText("Ends Oct 17")).toBeInTheDocument();
  expect(within(sprint).getByText("3 open")).toBeInTheDocument();

  await user.click(within(sprint).getByRole("button", { name: "Only work in progress" }));
  expect(within(sprint).getByRole("checkbox", { name: /Item b1/ })).not.toBeChecked();
  expect(within(sprint).getByRole("checkbox", { name: /Item f1/ })).toBeChecked();
  expect(within(sprint).queryByText(/will be marked done/)).not.toBeInTheDocument();
  expect(within(sprint).getByText(/1 of 2 items ticked, 3 open work items/)).toBeInTheDocument();
});

test("the agent's regroup is cancellable, and its failure is shown beside the button", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft());
  mockApi.agentInitiativeSuggestions.mockImplementation(
    (_days, signal) =>
      new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      }),
  );
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Ask the agent to regroup" }));
  await user.click(await screen.findByRole("button", { name: /Stop waiting/ }));
  expect(await screen.findByText(/Stopped waiting/)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();

  mockApi.agentInitiativeSuggestions.mockRejectedValueOnce(
    new ApiError(502, "The agent ran under loregarden's runtime (cursor) and failed: bad key"),
  );
  await user.click(screen.getByRole("button", { name: "Ask the agent to regroup" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("runtime (cursor) and failed: bad key");
});

test("the agent's grouping replaces the draft, and the draft's edits come back after", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft());
  mockApi.agentInitiativeSuggestions.mockResolvedValue(
    draft({
      source: "agent",
      generated_at: "2026-10-03T00:01:00Z",
      suggestions: [
        {
          key: "agent:0",
          kind: "theme",
          title: "A canvas that scales",
          description: "",
          rationale: "Both are canvas work.",
          items: [item("m1"), item("m3")],
          empties: [],
          target_date: null,
        },
      ],
      ungrouped: [item("m2")],
      warnings: ["“Extra” named 1 id(s) that are not open theme candidates, left out: nope"],
    }),
  );
  const user = userEvent.setup();
  renderPage();

  const canvas = await themeCard();
  await user.type(within(canvas).getByRole("textbox", { name: "Description" }), "kept");
  await user.click(screen.getByRole("button", { name: "Ask the agent to regroup" }));

  expect(await screen.findByDisplayValue("A canvas that scales")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: /Create “A canvas that scales”/ })).toBeChecked();
  expect(screen.getByText(/left out: nope/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Back to keyword draft" }));
  expect(within(await themeCard()).getByRole("textbox", { name: "Description" })).toHaveValue("kept");
});

test("with the pace already spent on a sprint, it says why no new one is offered", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(
    draft({
      suggestions: [],
      ungrouped: [],
      sprint: { days: 14, capacity: 10, committed: 34, planned: 0, basis: "workspace_throughput" },
    }),
  );
  renderPage();

  expect(await screen.findByText(/34 work items already sit in a sprint-style initiative/)).toBeInTheDocument();
});

test("nothing to suggest says why, and is not the error state", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft({ suggestions: [], ungrouped: [] }));
  renderPage();

  expect(await screen.findByText(/Nothing to suggest/)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /create/i })).not.toBeInTheDocument();
});

test("a failed draft offers a retry", async () => {
  mockApi.initiativeSuggestions.mockRejectedValueOnce(new ApiError(500, "boom"));
  mockApi.initiativeSuggestions.mockResolvedValueOnce(draft());
  const user = userEvent.setup();
  renderPage();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("boom");
  await user.click(within(alert).getByRole("button", { name: "Retry" }));
  expect(await screen.findByDisplayValue("Canvas work")).toBeInTheDocument();
});

test("a 409 on create redraws the suggestions instead of leaving a stale draft", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(draft());
  mockApi.applyInitiativeSuggestions.mockRejectedValue(new ApiError(409, "lg-f1 already belongs to init-x-1"));
  const user = userEvent.setup();
  renderPage();

  await themeCard();
  await user.click(screen.getByRole("button", { name: "Review and create…" }));
  await user.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(mockApi.initiativeSuggestions).toHaveBeenCalledTimes(2));
  expect(navigateToInitiative).not.toHaveBeenCalled();
  expect(navigateToPage).not.toHaveBeenCalled();
});
