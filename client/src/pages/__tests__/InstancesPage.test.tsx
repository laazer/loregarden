import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { localInstancesApi } from "../../api/localInstancesApi";
import type { LocalInstance, WorkspaceTemplates } from "../../api/localInstancesTypes";
import { InstancesPage } from "../InstancesPage";

jest.mock("../../api/localInstancesApi", () => ({
  localInstancesApi: {
    list: jest.fn(),
    templates: jest.fn(),
    launch: jest.fn(),
    logs: jest.fn(),
    stop: jest.fn(),
    workspaceTemplates: jest.fn(),
    createTemplate: jest.fn(),
    replaceTemplate: jest.fn(),
    deleteTemplate: jest.fn(),
  },
}));

const mockApi = localInstancesApi as jest.Mocked<typeof localInstancesApi>;

const instance = (overrides: Partial<LocalInstance> = {}): LocalInstance => ({
  id: "api-feat-a1",
  project: "shop",
  name: "api-feat",
  kind: "server",
  role: "branch",
  template: "shop/api",
  managed: true,
  url: "http://127.0.0.1:8101",
  health_path: "/health",
  ready_timeout_seconds: 120,
  log_path: "/r/x.log",
  target_instance_id: null,
  labels: {},
  started_at: "2026-09-27T00:00:00Z",
  exit_code: null,
  last_error: null,
  state: "ready",
  ...overrides,
});

const workspace = (overrides: Partial<WorkspaceTemplates> = {}): WorkspaceTemplates => ({
  slug: "shop",
  name: "Shop",
  repo_root: "/w/shop",
  template_file: "/w/shop/.loregarden/instances.yaml",
  file_error: "",
  conflicts: [],
  entries: [
    {
      name: "api",
      qualified_name: "shop/api",
      origin: "file",
      kind: "server",
      description: "The API",
      spec: null,
      shadowed_by: null,
      error: "",
      launchable: true,
    },
  ],
  ...overrides,
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InstancesPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.templates.mockResolvedValue([
    { name: "shop/api", kind: "server", description: "", params: [] },
    { name: "blog/web", kind: "client", description: "", params: [] },
  ]);
  mockApi.workspaceTemplates.mockResolvedValue([workspace(), workspace({ slug: "blog", name: "Blog", entries: [] })]);
  jest.spyOn(window, "confirm").mockReturnValue(true);
});

it("groups running instances by workspace", async () => {
  mockApi.list.mockResolvedValue({
    instances: [instance(), instance({ id: "web-1", project: "blog", name: "web" })],
    unreadable: [],
  });
  renderPage();
  expect(await screen.findByRole("heading", { name: "shop" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "blog" })).toBeInTheDocument();
});

it("filters to one workspace: its instances, its templates, its launch choices", async () => {
  mockApi.list.mockResolvedValue({
    instances: [instance(), instance({ id: "web-1", project: "blog", name: "web" })],
    unreadable: [],
  });
  renderPage();
  await screen.findByRole("heading", { name: "shop" });
  fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: "blog" } });
  expect(screen.queryByRole("heading", { name: "shop" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Blog templates" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Shop templates" })).not.toBeInTheDocument();
  const form = screen.getByRole("form", { name: "Launch an instance" });
  expect(within(form).getAllByRole("option").map((o) => o.textContent)).toEqual([
    expect.stringContaining("blog/web"),
  ]);
});

it("tells an empty machine apart from a filter that hides everything", async () => {
  mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
  renderPage();
  expect(await screen.findByText(/Nothing is running/)).toBeInTheDocument();
});

it("says so when no instance matches the filters", async () => {
  mockApi.list.mockResolvedValue({ instances: [instance()], unreadable: [] });
  renderPage();
  await screen.findByRole("heading", { name: "shop" });
  fireEvent.change(screen.getByLabelText("State"), { target: { value: "exited" } });
  expect(screen.getByText("No instance matches these filters.")).toBeInTheDocument();
});

it("reports a broken repo file on its workspace", async () => {
  mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
  mockApi.workspaceTemplates.mockResolvedValue([workspace({ file_error: "templates.0.name: lowercase only" })]);
  renderPage();
  expect(await screen.findByText(/The repo file cannot be used: templates.0.name/)).toBeInTheDocument();
});

it("explains why a shadowed template will not launch", async () => {
  mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
  mockApi.workspaceTemplates.mockResolvedValue([
    workspace({
      entries: [
        { ...workspace().entries[0] },
        { ...workspace().entries[0], origin: "stored", launchable: false, shadowed_by: "file" },
      ],
    }),
  ]);
  renderPage();
  expect(await screen.findByText(/Not used: the repo file defines api too/)).toBeInTheDocument();
});

it("creates a template from the editor, command as argv", async () => {
  mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
  mockApi.createTemplate.mockResolvedValue(workspace());
  renderPage();
  const panel = (await screen.findByRole("heading", { name: "Shop templates" })).closest("section") as HTMLElement;
  fireEvent.click(within(panel).getByRole("button", { name: "New template" }));
  const form = within(panel).getByRole("form", { name: "New template" });
  const create = within(form).getByRole("button", { name: "Create template" });
  expect(create).toBeDisabled();

  fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "docs" } });
  fireEvent.change(within(form).getByLabelText("Command"), {
    target: { value: "python3\n-m\nhttp.server\n{port}" },
  });
  fireEvent.change(within(form).getByLabelText("Environment"), { target: { value: "MODE=dev" } });
  fireEvent.click(create);

  await waitFor(() =>
    expect(mockApi.createTemplate).toHaveBeenCalledWith(
      "shop",
      expect.objectContaining({
        name: "docs",
        command: ["python3", "-m", "http.server", "{port}"],
        env: { MODE: "dev" },
        cwd: ".",
        target: null,
      }),
    ),
  );
});

it("shows the server's refusal inside the editor", async () => {
  mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
  mockApi.createTemplate.mockRejectedValue(new Error("cwd must be relative to the worktree"));
  renderPage();
  const panel = (await screen.findByRole("heading", { name: "Shop templates" })).closest("section") as HTMLElement;
  fireEvent.click(within(panel).getByRole("button", { name: "New template" }));
  const form = within(panel).getByRole("form", { name: "New template" });
  fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "docs" } });
  fireEvent.change(within(form).getByLabelText("Command"), { target: { value: "x" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create template" }));
  expect(await within(form).findByRole("alert")).toHaveTextContent("cwd must be relative");
});

it("edits and deletes only templates saved here", async () => {
  mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
  mockApi.deleteTemplate.mockResolvedValue(undefined);
  mockApi.workspaceTemplates.mockResolvedValue([
    workspace({
      entries: [
        { ...workspace().entries[0] },
        {
          ...workspace().entries[0],
          name: "docs",
          qualified_name: "shop/docs",
          origin: "stored",
          spec: {
            name: "docs",
            kind: "server",
            description: "",
            command: ["x"],
            cwd: ".",
            env: {},
            health_path: "/",
            ready_timeout_seconds: 120,
            port_range: [8100, 8999],
            params: [],
            target: null,
          },
        },
      ],
    }),
  ]);
  renderPage();
  const docsRow = (await screen.findByText("docs")).closest("tr") as HTMLElement;
  const apiRow = screen.getByText("api").closest("tr") as HTMLElement;
  expect(within(apiRow).queryByRole("button")).not.toBeInTheDocument();
  fireEvent.click(within(docsRow).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(mockApi.deleteTemplate).toHaveBeenCalledWith("shop", "docs"));
});
