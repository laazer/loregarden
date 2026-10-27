import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api, type InitiativeMilestone, type InitiativeView } from "../../api/client";
import { useReaderStore } from "../../state/readerStore";
import { InitiativesPage } from "../InitiativesPage";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function milestone(overrides: Partial<InitiativeMilestone> = {}): InitiativeMilestone {
  return {
    id: "m1",
    external_id: "lor-m-1",
    title: "Backend half",
    state: "done",
    workspace_slug: "loregarden",
    work_item_type: "milestone",
    member: false,
    home_milestone: "",
    ...overrides,
  };
}

function initiative(overrides: Partial<InitiativeView> = {}): InitiativeView {
  return {
    id: "i1",
    external_id: "init-auth-1",
    title: "Auth migration",
    description: "",
    state: "in_progress",
    priority: 3,
    milestones: [milestone()],
    progress: { resolved: 1, total: 2 },
    workspaces: ["loregarden"],
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InitiativesPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.attachableMilestones.mockResolvedValue([]);
});

test("an empty list says what an initiative is and offers to create one", async () => {
  mockApi.initiatives.mockResolvedValue([]);
  mockApi.createInitiative.mockResolvedValue({} as never);
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "create an empty one" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "  Q4 goal ");
  await user.click(screen.getByRole("button", { name: "Create initiative" }));

  await waitFor(() =>
    expect(mockApi.createInitiative).toHaveBeenCalledWith({ title: "Q4 goal", description: "" }),
  );
  expect(mockApi.setMilestoneInitiative).not.toHaveBeenCalled();
});

test("milestones with no initiative are listed by workspace, finished ones hidden", async () => {
  mockApi.initiatives.mockResolvedValue([]);
  mockApi.attachableMilestones.mockResolvedValue([
    milestone({ id: "a", title: "Open loregarden work", state: "in_progress" }),
    milestone({ id: "b", title: "Open blobert work", state: "backlog", workspace_slug: "blobert" }),
    milestone({ id: "c", title: "Finished work", state: "done" }),
  ]);
  const user = userEvent.setup();
  renderPage();

  expect(await screen.findByRole("heading", { name: "blobert" })).toBeInTheDocument();
  expect(screen.getByText("Open loregarden work")).toBeInTheDocument();
  expect(screen.queryByText("Finished work")).not.toBeInTheDocument();

  await user.click(screen.getByRole("checkbox", { name: /Show finished/ }));

  expect(screen.getByText("Finished work")).toBeInTheDocument();
});

test("ticked milestones are attached to the initiative created from them", async () => {
  mockApi.initiatives.mockResolvedValue([]);
  mockApi.attachableMilestones.mockResolvedValue([
    milestone({ id: "a", title: "Server half", state: "in_progress" }),
    milestone({ id: "b", title: "Client half", state: "backlog", workspace_slug: "blobert" }),
  ]);
  mockApi.createInitiative.mockResolvedValue({ id: "new-init" } as never);
  mockApi.setMilestoneInitiative.mockResolvedValue({} as never);
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("checkbox", { name: /Server half/ }));
  await user.click(screen.getByRole("checkbox", { name: /Client half/ }));
  await user.click(screen.getByRole("button", { name: "Group 2 into initiative" }));
  expect(screen.getByText("Attaches 2 milestones")).toBeInTheDocument();
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Auth");
  await user.click(screen.getByRole("button", { name: "Create initiative" }));

  await waitFor(() => expect(mockApi.setMilestoneInitiative).toHaveBeenCalledTimes(2));
  expect(mockApi.setMilestoneInitiative).toHaveBeenCalledWith("a", "new-init");
  expect(mockApi.setMilestoneInitiative).toHaveBeenCalledWith("b", "new-init");
});

test("an initiative shows its milestones across workspaces with progress", async () => {
  mockApi.initiatives.mockResolvedValue([
    initiative({
      milestones: [milestone(), milestone({ id: "m2", title: "Client half", workspace_slug: "blobert", state: "backlog" })],
      workspaces: ["blobert", "loregarden"],
    }),
  ]);
  renderPage();

  expect(await screen.findByRole("heading", { name: "Auth migration" })).toBeInTheDocument();
  expect(screen.getByText("blobert")).toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "Auth migration progress" })).toHaveAttribute(
    "aria-valuenow",
    "50",
  );
  // Deleting is refused while milestones are attached; the server would refuse it too.
  expect(screen.getByRole("button", { name: "Delete initiative" })).toBeDisabled();
});

/** jsdom lays nothing out; give the brief the heights a browser would. */
function layOutBrief(scrollHeight: number, clientHeight: number) {
  jest.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(scrollHeight);
  jest.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(clientHeight);
}

afterEach(() => {
  jest.restoreAllMocks();
});

test("a markdown brief renders as structure, clamped, and the rest opens in the reader", async () => {
  layOutBrief(400, 58);
  const brief = [
    "Ship **one login** across workspaces.",
    "",
    "| Milestone | Contributes |",
    "|---|---|",
    "| `lg-auth-1` | Sessions |",
    "",
    "Background. ".repeat(60),
  ].join("\n");
  mockApi.initiatives.mockResolvedValue([initiative({ description: brief })]);
  renderPage();

  expect(await screen.findByRole("table")).toBeInTheDocument();
  expect(screen.getByText("one login").tagName).toBe("STRONG");
  expect(screen.queryByText(/\*\*one login\*\*/)).not.toBeInTheDocument();
  const more = screen.getByRole("button", { name: "Read the brief for Auth migration" });
  expect(more).toHaveTextContent("Read brief");
  await userEvent.setup().click(more);
  expect(useReaderStore.getState().document).toMatchObject({ title: "Auth migration", subtitle: "init-auth-1", content: brief });
});

test("a brief that fits in three lines offers nothing more to read", async () => {
  layOutBrief(40, 58);
  mockApi.initiatives.mockResolvedValue([initiative({ description: "Ship one login across workspaces." })]);
  renderPage();

  expect(await screen.findByText("Ship one login across workspaces.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Read the brief/ })).not.toBeInTheDocument();
});

test("attach and detach reparent the milestone", async () => {
  mockApi.initiatives.mockResolvedValue([initiative()]);
  mockApi.attachableMilestones.mockResolvedValue([
    milestone({ id: "free", title: "Loose milestone", workspace_slug: "blobert" }),
  ]);
  mockApi.setMilestoneInitiative.mockResolvedValue({} as never);
  const user = userEvent.setup();
  renderPage();

  const picker = await screen.findByRole("combobox", { name: "Milestone to attach to Auth migration" });
  await waitFor(() => expect(screen.getByRole("option", { name: /Loose milestone/ })).toBeInTheDocument());
  await user.selectOptions(picker, "free");
  await user.click(screen.getByRole("button", { name: "Attach" }));
  await waitFor(() => expect(mockApi.setMilestoneInitiative).toHaveBeenCalledWith("free", "i1"));

  // Detach lives in the row's menu, not on the row.
  expect(screen.queryByRole("button", { name: /Detach/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "lor-m-1 actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Detach from this initiative" }));
  await waitFor(() => expect(mockApi.setMilestoneInitiative).toHaveBeenCalledWith("m1", null));
});

test("a detach in flight replaces the row's menu, so it cannot fire twice", async () => {
  mockApi.initiatives.mockResolvedValue([initiative()]);
  mockApi.setMilestoneInitiative.mockReturnValue(new Promise(() => {}));
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "lor-m-1 actions" }));
  await user.click(screen.getByRole("menuitem", { name: "Detach from this initiative" }));

  expect(await screen.findByRole("status")).toHaveTextContent("Detaching…");
  expect(screen.queryByRole("button", { name: "lor-m-1 actions" })).not.toBeInTheDocument();
  expect(mockApi.setMilestoneInitiative).toHaveBeenCalledTimes(1);
});

test("a failed load says so instead of showing the empty state", async () => {
  mockApi.initiatives.mockRejectedValue(new Error("boom"));
  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent("Initiatives could not be loaded");
  expect(screen.queryByRole("button", { name: "Create the first initiative" })).not.toBeInTheDocument();
});

describe("a sprint-style initiative", () => {
  const sprint = () =>
    initiative({
      id: "s1",
      title: "Sprint Oct 3",
      milestones: [milestone({ id: "f1", title: "Drag nodes", work_item_type: "feature", state: "in_progress" })],
    });

  test("moves a feature back to a milestone in its workspace, done ones included", async () => {
    mockApi.initiatives.mockResolvedValue([sprint()]);
    mockApi.attachableMilestones.mockResolvedValue([
      milestone({ id: "m9", external_id: "lg-m9", title: "Canvas", state: "done" }),
      milestone({ id: "x1", external_id: "lor-x-1", title: "Elsewhere", workspace_slug: "lore-eden" }),
    ]);
    mockApi.setMilestoneInitiative.mockResolvedValue({} as never);
    const user = userEvent.setup();
    renderPage();

    const move = await screen.findByRole("combobox", { name: "Move Drag nodes to a milestone" });
    expect(within(move).queryByText(/Elsewhere/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Detach Drag nodes/ })).not.toBeInTheDocument();
    await user.selectOptions(move, "lg-m9 — Canvas (done)");
    await waitFor(() => expect(mockApi.setMilestoneInitiative).toHaveBeenCalledWith("f1", "m9"));
  });

  test("finds open work by search and adds it to the sprint", async () => {
    mockApi.initiatives.mockResolvedValue([sprint()]);
    mockApi.initiativeAddableWork.mockResolvedValue([
      {
        ...milestone({ id: "f2", external_id: "lg-f2", title: "Zoom nodes", work_item_type: "feature" }),
        from_milestone: "lg-m9",
        cost: 2,
      },
    ]);
    mockApi.setMilestoneInitiative.mockResolvedValue({} as never);
    const user = userEvent.setup();
    renderPage();

    await user.type(await screen.findByRole("searchbox", { name: "Add a feature or bug to this sprint" }), "nodes");
    const add = await screen.findByRole("button", { name: "Add lg-f2 to Sprint Oct 3" });
    expect(screen.getByText("leaves lg-m9")).toBeInTheDocument();
    expect(mockApi.initiativeAddableWork).toHaveBeenCalledWith("s1", "nodes");
    await user.click(add);
    await waitFor(() => expect(mockApi.setMilestoneInitiative).toHaveBeenCalledWith("f2", "s1"));
  });
});

describe("an initiative at volume", () => {
  /** The Pre-TinkerCG shape: tracked members spread over many home milestones, a few open. */
  function trackedWork(count: number, homes: number): InitiativeMilestone[] {
    return Array.from({ length: count }, (_, n) =>
      milestone({
        id: `t${n}`,
        external_id: `lg-feat-${n}`,
        title: `Feature ${n}`,
        work_item_type: "feature",
        member: true,
        home_milestone: `lg-home-${n % homes}`,
        state: n === 0 ? "blocked" : n === 1 ? "in_progress" : n % 10 === 9 ? "done" : "backlog",
      }),
    );
  }

  test("groups rows by home milestone, opening only the groups with work in flight", async () => {
    const rows = trackedWork(40, 8);
    mockApi.initiatives.mockResolvedValue([initiative({ milestones: rows })]);
    const user = userEvent.setup();
    renderPage();

    const list = await screen.findByRole("list", { name: "Auth migration by milestone" });
    // 36 open rows over 8 homes; the 4 done ones wait behind the toggle.
    expect(within(list).getAllByRole("group")).toHaveLength(8);
    expect(screen.getByRole("status")).toHaveTextContent("8 groups");
    // lg-home-0 holds the blocked row and lg-home-1 the in-progress one: both open, first.
    const [first, second] = within(list).getAllByRole("group");
    expect(first).toHaveTextContent(/lg-home-05 items1 blocked/);
    expect(within(first).getByRole("button", { name: /lg-feat-0/ })).toBeInTheDocument();
    expect(second).toHaveTextContent(/lg-home-14 items1 in progress/);
    // A group with nothing in flight is collapsed: its rows are not rendered at all,
    // so only lg-home-0's 5 and lg-home-1's 4 open rows are on the page.
    expect(screen.queryByRole("button", { name: /lg-feat-2 Feature 2$/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^lg-feat-\d+ Feature \d+$/ })).toHaveLength(9);
    // Grouped rows don't repeat "Tracked" or their home on every line.
    expect(screen.queryByText("Tracked")).not.toBeInTheDocument();
    expect(screen.queryByText(/in lg-home-0/)).not.toBeInTheDocument();
    // Fewer than 50 rows: no filter.
    expect(screen.queryByRole("searchbox", { name: /Filter/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show 4 resolved" }));
    expect(screen.getByRole("button", { name: "Hide 4 resolved" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(list).getByText("lg-home-2"));
    expect(screen.getByRole("button", { name: /lg-feat-2 Feature 2$/ })).toBeInTheDocument();
  });

  test("a card past fifty rows gets a filter that opens the groups it matches", async () => {
    const rows = trackedWork(157, 36);
    mockApi.initiatives.mockResolvedValue([initiative({ milestones: rows })]);
    const user = userEvent.setup();
    renderPage();

    const filter = await screen.findByRole("searchbox", { name: "Filter Auth migration by id, title or milestone" });
    // 36 groups: the two with work in flight plus the ten largest quiet ones; the rest wait.
    const list = screen.getByRole("list", { name: "Auth migration by milestone" });
    expect(within(list).getAllByRole("group")).toHaveLength(12);
    const more = screen.getByRole("button", { name: "Show 24 more milestones" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    expect(within(list).getAllByRole("group")).toHaveLength(36);
    expect(screen.getByRole("button", { name: "Show fewer milestones" })).toHaveAttribute("aria-expanded", "true");

    await user.type(filter, "feature 15");
    // "Feature 15" and "Feature 150"–"Feature 156": the done one (159 would be) is hidden.
    expect(screen.getByRole("status")).toHaveTextContent("8 of 157 match");
    for (const n of [15, 150, 151, 152, 153, 154, 155, 156]) {
      expect(screen.getByRole("button", { name: new RegExp(`lg-feat-${n} Feature ${n}$`) })).toBeInTheDocument();
    }

    await user.clear(filter);
    await user.type(filter, "lg-home-35");
    expect(within(screen.getByRole("list", { name: "Auth migration by milestone" })).getAllByRole("group")).toHaveLength(1);

    await user.clear(filter);
    await user.type(filter, "nothing like this");
    expect(screen.getByText(/Nothing here matches “nothing like this”/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear the filter" }));
    expect(filter).toHaveValue("");
    expect(screen.getByRole("status")).toHaveTextContent("36 groups");
  });

  test("a card whose every row is resolved says so instead of showing nothing", async () => {
    const rows = trackedWork(20, 4).map((m) => ({ ...m, state: "done" as const }));
    mockApi.initiatives.mockResolvedValue([initiative({ milestones: rows })]);
    renderPage();

    expect(await screen.findByText("All 20 are resolved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show 20 resolved" })).toBeInTheDocument();
  });

  test("a long list of attached milestones stays flat: one group would only add a heading", async () => {
    const rows = Array.from({ length: 14 }, (_, n) =>
      milestone({ id: `m${n}`, external_id: `lg-m-${n}`, title: `Milestone ${n}`, state: "backlog" }),
    );
    mockApi.initiatives.mockResolvedValue([initiative({ milestones: rows })]);
    renderPage();

    expect(await screen.findByRole("button", { name: "lg-m-13 actions" })).toBeInTheDocument();
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^lg-m-\d+ actions$/ })).toHaveLength(14);
  });
});
