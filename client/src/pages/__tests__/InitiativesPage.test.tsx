import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api, type InitiativeMilestone, type InitiativeView } from "../../api/client";
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

  await user.click(screen.getByRole("button", { name: "Detach Backend half from this initiative" }));
  await waitFor(() => expect(mockApi.setMilestoneInitiative).toHaveBeenCalledWith("m1", null));
});

test("a failed load says so instead of showing the empty state", async () => {
  mockApi.initiatives.mockRejectedValue(new Error("boom"));
  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent("Initiatives could not be loaded");
  expect(screen.queryByRole("button", { name: "Create the first initiative" })).not.toBeInTheDocument();
});
