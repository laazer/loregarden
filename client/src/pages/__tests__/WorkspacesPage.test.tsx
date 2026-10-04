import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { api } from "../../api/client";
import { localInstancesApi } from "../../api/localInstancesApi";
import type {
  LocalInstance,
  TemplateSpec,
  WorkspaceIntegration,
  WorkspaceTemplates,
} from "../../api/localInstancesTypes";
import type { WorkspaceSummary } from "../../api/types";
import type { RepositoryState } from "../../api/workspaceRepositoryTypes";
import type { GatePresets } from "../../api/gatePresetTypes";
import type { OrchestrationProfileView } from "../../api/gateTypes";
import { useToastStore } from "../../state/toastStore";
import { LegacyInstancesRedirect, WorkspacesPage } from "../WorkspacesPage";

jest.mock("../../api/client");

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
    writeTemplateFile: jest.fn(),
    integration: jest.fn(),
    install: jest.fn(),
  },
}));

const mockApi = localInstancesApi as jest.Mocked<typeof localInstancesApi>;
const mockClient = api as jest.Mocked<typeof api>;

const GUARDRAIL = "node {loregarden_root}/.lefthook/scripts/ts_ux_states_check.cjs --repo {workspace_root}";
const RUFF = "uv run --directory server ruff check";
const PYTEST = "uv run --directory server pytest -q";
const CARGO = "cargo clippy --manifest-path Cargo.toml -- -D warnings";

const PRESETS: GatePresets = {
  repo_root: "/w/docs",
  toolchains: [
    {
      key: "python",
      label: "Python",
      directory: "server",
      detected: true,
      note: "",
      commands: [
        { command: RUFF, label: "Ruff lint", kind: "lint", default_on: true },
        { command: PYTEST, label: "pytest", kind: "test", default_on: false },
      ],
    },
    {
      key: "rust",
      label: "Rust",
      directory: ".",
      detected: false,
      note: "",
      commands: [{ command: CARGO, label: "clippy", kind: "lint", default_on: false }],
    },
  ],
};

const profile = (commands: string[] = [GUARDRAIL]): OrchestrationProfileView => ({
  slug: "shop",
  name: "shop",
  driver: "builtin",
  workflow_template: "",
  orchestrator_skill: "",
  gates_enabled: true,
  gates_configured: true,
  gates_commands: commands,
  gates_transition_script: "ci/gates.py",
  gates_transition_script_resolved: "",
  gates_autofix_commands: [],
  gates_autofix_agent_fallback: false,
  gates_autofix_max_agent_attempts: 0,
  gates_placeholders: {},
  gates_suggested_commands: [],
  max_stages_per_run: 0, source_path: null,
});

const probeAs = (state: RepositoryState, detail = "") =>
  mockClient.probeRepository.mockImplementation((path) => Promise.resolve({ repo_root: path, state, detail }));

const openAddDialog = async (path: string) => {
  fireEvent.click(await screen.findByRole("button", { name: "Add workspace" }));
  const dialog = await screen.findByRole("dialog", { name: "Add workspace" });
  fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Docs" } });
  fireEvent.change(within(dialog).getByLabelText("Repo path"), { target: { value: path } });
  return dialog;
};

const summary = (slug: string, name: string, overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary => ({
  id: `id-${slug}`,
  slug,
  name,
  repo_path: `/w/${slug}`,
  repo_root: `/w/${slug}`,
  repo_exists: true,
  repo_state: "repository",
  ticket_count: 3,
  blocked_count: 0,
  workflow_template_slug: "loregarden-tdd",
  archived_at: null,
  cli_adapter: "claude",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
  ...overrides,
});

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
  file_exists: true,
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

function Where() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

function renderPage(path = "/workspaces") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/workspaces/*" element={<WorkspacesPage />} />
          <Route path="/instances/*" element={<LegacyInstancesRedirect />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const renderInstances = () => renderPage("/workspaces/instances");

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.templates.mockResolvedValue([
    { name: "shop/api", kind: "server", description: "", params: [] },
    { name: "blog/web", kind: "client", description: "", params: [] },
  ]);
  mockApi.workspaceTemplates.mockResolvedValue([workspace(), workspace({ slug: "blog", name: "Blog", entries: [] })]);
  mockClient.workspaces.mockResolvedValue([summary("shop", "Shop"), summary("blog", "Blog")]);
  mockClient.workflowTemplates.mockResolvedValue([]);
  mockClient.gatePresetsForPath.mockResolvedValue(PRESETS);
  mockClient.gatePresetsForWorkspace.mockResolvedValue(PRESETS);
  mockClient.orchestrationProfile.mockResolvedValue(profile());
  mockClient.updateWorkspaceGates.mockImplementation((_slug, body) => Promise.resolve(profile(body.commands)));
  mockClient.probeRepository.mockImplementation((path) =>
    Promise.resolve({ repo_root: path, state: "repository", detail: `${path} is a git repository` }),
  );
  mockClient.browseDirectory.mockResolvedValue({
    current_path: "/w",
    repo_path: ".",
    parent_path: null,
    repo_root: "/w",
    entries: [],
  });
  jest.spyOn(window, "confirm").mockReturnValue(true);
  mockApi.integration.mockImplementation((slug) => Promise.resolve(integration(slug)));
});

const integration = (slug: string, overrides: Partial<WorkspaceIntegration> = {}): WorkspaceIntegration => ({
  slug,
  repo_root: `/w/${slug}`,
  installers: [
    { installer: "hooks", state: "unavailable", detail: `/w/${slug} has no lefthook.yml (install lefthook there first)` },
    { installer: "docs", state: "missing", detail: `missing file: /w/${slug}/AGENTS.md managed section` },
  ],
  ...overrides,
});

const SPEC: TemplateSpec = {
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
};

async function setupPanel(name = "Shop"): Promise<HTMLElement> {
  return (await screen.findByRole("heading", { name: `${name} setup` })).closest("section") as HTMLElement;
}

it("groups running instances by workspace", async () => {
  mockApi.list.mockResolvedValue({
    instances: [instance(), instance({ id: "web-1", project: "blog", name: "web" })],
    unreadable: [],
  });
  renderInstances();
  expect(await screen.findByRole("heading", { name: "shop" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "blog" })).toBeInTheDocument();
});

it("filters to one workspace: its instances and its launch choices", async () => {
  mockApi.list.mockResolvedValue({
    instances: [instance(), instance({ id: "web-1", project: "blog", name: "web" })],
    unreadable: [],
  });
  renderInstances();
  await screen.findByRole("heading", { name: "shop" });
  fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: "blog" } });
  expect(screen.queryByRole("heading", { name: "shop" })).not.toBeInTheDocument();
  const form = screen.getByRole("form", { name: "Launch an instance" });
  expect(within(form).getAllByRole("option").map((o) => o.textContent)).toEqual([
    expect.stringContaining("blog/web"),
  ]);
});

it("tells an empty machine apart from a filter that hides everything", async () => {
  mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
  renderInstances();
  expect(await screen.findByText(/Nothing is running/)).toBeInTheDocument();
});

it("says so when no instance matches the filters", async () => {
  mockApi.list.mockResolvedValue({ instances: [instance()], unreadable: [] });
  renderInstances();
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

describe("workspace setup", () => {
  beforeEach(() => {
    mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
    useToastStore.setState({ toasts: [] });
  });

  it("offers what can be installed and says why the rest cannot", async () => {
    renderPage();
    const panel = await setupPanel();
    const hooks = (await within(panel).findByText("Pre-commit gates")).closest("tr") as HTMLElement;
    expect(await within(hooks).findByText("Cannot install")).toBeInTheDocument();
    expect(within(hooks).getByText(/has no lefthook.yml/)).toBeInTheDocument();
    expect(within(hooks).queryByRole("button")).not.toBeInTheDocument();
    const docs = within(panel).getByText("Agent instructions").closest("tr") as HTMLElement;
    expect(within(docs).getByRole("button", { name: "Install" })).toBeEnabled();
  });

  it("installs only after confirming, and blocks a second click while it runs", async () => {
    let finish: (value: WorkspaceIntegration) => void = () => {};
    mockApi.install.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    renderPage();
    const panel = await setupPanel();
    const install = await within(panel).findByRole("button", { name: "Install" });

    (window.confirm as jest.Mock).mockReturnValueOnce(false);
    fireEvent.click(install);
    expect(mockApi.install).not.toHaveBeenCalled();

    fireEvent.click(install);
    expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining("AGENTS.md in /w/shop"));
    const running = await within(panel).findByRole("button", { name: "Installing…" });
    expect(running).toBeDisabled();
    expect(mockApi.install).toHaveBeenCalledTimes(1);
    expect(mockApi.install).toHaveBeenCalledWith("shop", "docs");

    const done = integration("shop");
    done.installers[1] = { installer: "docs", state: "current", detail: "ok" };
    finish(done);
    expect(await within(panel).findByText("Installed")).toBeInTheDocument();
  });

  it("reports a refused install", async () => {
    mockApi.install.mockRejectedValue(new Error("AGENTS.md is not writable"));
    renderPage();
    const panel = await setupPanel();
    fireEvent.click(await within(panel).findByRole("button", { name: "Install" }));
    await waitFor(() => expect(within(panel).getByRole("button", { name: "Install" })).toBeEnabled());
    expect(useToastStore.getState().toasts).toContainEqual(
      expect.objectContaining({ tone: "error", title: "Install Agent instructions failed", message: "AGENTS.md is not writable" }),
    );
  });

  it("shows loregarden's own repository as built in, with nothing to install", async () => {
    const detail = "This is loregarden's own repository";
    mockApi.integration.mockResolvedValue(
      integration("shop", {
        installers: [
          { installer: "hooks", state: "built_in", detail },
          { installer: "docs", state: "built_in", detail },
        ],
      }),
    );
    renderPage();
    const panel = await setupPanel();
    expect(await within(panel).findAllByText("Built in")).toHaveLength(2);
    for (const part of ["Pre-commit gates", "Agent instructions"]) {
      const row = within(panel).getByText(part).closest("tr") as HTMLElement;
      expect(within(row).queryByRole("button")).not.toBeInTheDocument();
      expect(within(row).getByText("Built in").closest("td")).not.toHaveClass("instances-warning");
    }
    const chips = screen.getAllByText((_, el) => el?.classList.contains("instances-chip") === true && el.textContent === "gates built in");
    for (const chip of chips) expect(chip).toHaveClass("instances-chip--ok");
  });

  it("says when the check itself failed, instead of showing nothing installed", async () => {
    mockApi.integration.mockRejectedValue(new Error("server unreachable"));
    renderPage();
    const panel = await setupPanel();
    expect(await within(panel).findByRole("alert")).toHaveTextContent("server unreachable");
    expect(within(panel).queryByText("Not installed")).not.toBeInTheDocument();
  });

  it("writes saved templates to the repo file when there is none", async () => {
    const stored = { ...workspace().entries[0], name: "docs", qualified_name: "shop/docs", origin: "stored" as const, spec: SPEC };
    mockApi.workspaceTemplates.mockResolvedValue([workspace({ file_exists: false, entries: [stored] })]);
    mockApi.writeTemplateFile.mockResolvedValue(workspace());
    renderPage();
    const panel = await setupPanel();
    expect(await within(panel).findByText(/1 saved here can be written/)).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Write file" }));
    await waitFor(() => expect(mockApi.writeTemplateFile).toHaveBeenCalledWith("shop"));
  });

  it("offers no file write when the repo already has one", async () => {
    renderPage();
    const panel = await setupPanel();
    const row = (await within(panel).findByText("Launch templates")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Present")).toBeInTheDocument();
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("toolchain gates on the card", () => {
  beforeEach(() => {
    mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
    useToastStore.setState({ toasts: [] });
    mockClient.workspaces.mockResolvedValue([summary("shop", "Shop")]);
    mockApi.workspaceTemplates.mockResolvedValue([workspace()]);
  });

  const gatesPanel = async () => {
    fireEvent.click(await screen.findByText("Shop", { selector: ".instances-setup-name" }));
    return (await screen.findByRole("heading", { name: "Shop toolchain gates" })).closest("section") as HTMLElement;
  };

  it("loads nothing for a card's gates until the card is opened", async () => {
    renderPage();
    await screen.findByText("Shop", { selector: ".instances-setup-name" });
    expect(mockClient.gatePresetsForWorkspace).not.toHaveBeenCalled();
    await gatesPanel();
    expect(mockClient.gatePresetsForWorkspace).toHaveBeenCalledWith("shop");
  });

  const report = (outcome: "passed" | "failed", command = PYTEST) => ({
    repo_root: "/w/shop",
    transition: "implement→verify",
    results: [{ template: command, command, outcome, message: "", stdout: "", stderr: "3 failed", duration_ms: 900 }],
  });

  it("ticks what is configured and saves an addition only after it passes on the current code", async () => {
    mockClient.orchestrationProfile.mockResolvedValue(profile([GUARDRAIL, RUFF]));
    mockClient.testWorkspaceGates.mockResolvedValue(report("passed"));
    renderPage();
    const panel = await gatesPanel();
    expect(await within(panel).findByRole("checkbox", { name: /Ruff lint/ })).toBeChecked();
    expect(within(panel).getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(within(panel).getByRole("checkbox", { name: /pytest/ }));
    fireEvent.click(within(panel).getByRole("button", { name: "Check and save" }));
    await waitFor(() =>
      expect(mockClient.updateWorkspaceGates).toHaveBeenCalledWith("shop", {
        enabled: true,
        commands: [GUARDRAIL, RUFF, PYTEST],
        transition_script: "ci/gates.py",
      }),
    );
    expect(mockClient.testWorkspaceGates).toHaveBeenCalledWith("shop", { commands: [PYTEST] });
  });

  it("holds a failing addition back and shows why, until saved anyway", async () => {
    mockClient.testWorkspaceGates.mockResolvedValue(report("failed"));
    renderPage();
    const panel = await gatesPanel();
    fireEvent.click(await within(panel).findByRole("checkbox", { name: /pytest/ }));
    fireEvent.click(within(panel).getByRole("button", { name: "Check and save" }));
    const alert = await within(panel).findByRole("alert");
    expect(within(alert).getByText("3 failed")).toBeInTheDocument();
    expect(mockClient.updateWorkspaceGates).not.toHaveBeenCalled();

    fireEvent.click(within(panel).getByRole("button", { name: "Save anyway" }));
    await waitFor(() => expect(mockClient.updateWorkspaceGates).toHaveBeenCalledTimes(1));
    expect(mockClient.testWorkspaceGates).toHaveBeenCalledTimes(1);
  });

  it("removes an unticked preset without running anything, and leaves the guardrails", async () => {
    mockClient.orchestrationProfile.mockResolvedValue(profile([GUARDRAIL, RUFF]));
    renderPage();
    const panel = await gatesPanel();
    fireEvent.click(await within(panel).findByRole("checkbox", { name: /Ruff lint/ }));
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(mockClient.updateWorkspaceGates).toHaveBeenCalledWith("shop", expect.objectContaining({ commands: [GUARDRAIL] })),
    );
    expect(mockClient.testWorkspaceGates).not.toHaveBeenCalled();
  });

  it("says when the gates could not be loaded", async () => {
    mockClient.gatePresetsForWorkspace.mockRejectedValue(new Error("server unreachable"));
    renderPage();
    const panel = await gatesPanel();
    expect(await within(panel).findByRole("alert")).toHaveTextContent("server unreachable");
  });
});

describe("tabs", () => {
  beforeEach(() => mockApi.list.mockResolvedValue({ instances: [instance()], unreadable: [] }));

  it("opens on the workspace list, and the Instances tab is in the URL", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: /Active workspaces/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Workspaces" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("heading", { name: /Running/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Instances" }));
    expect(await screen.findByRole("heading", { name: /Running/ })).toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent("/workspaces/instances");
    expect(screen.getByRole("link", { name: "Instances" })).toHaveAttribute("aria-current", "page");
  });

  it("sends the old /instances address to the Instances tab", async () => {
    renderPage("/instances");
    expect(await screen.findByRole("heading", { name: /Running/ })).toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent("/workspaces/instances");
  });

  it("points an empty launch list at the Workspaces tab", async () => {
    mockApi.templates.mockResolvedValue([]);
    renderInstances();
    fireEvent.click(await screen.findByRole("link", { name: "Add one on the Workspaces tab." }));
    expect(await screen.findByRole("heading", { name: /Active workspaces/ })).toBeInTheDocument();
  });
});

describe("workspace list", () => {
  beforeEach(() => {
    mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
    useToastStore.setState({ toasts: [] });
  });

  it("summarises each workspace's tickets and a missing repo on its card", async () => {
    mockClient.workspaces.mockResolvedValue([
      summary("shop", "Shop", { ticket_count: 5, blocked_count: 2 }),
      summary("blog", "Blog", { ticket_count: 1, repo_exists: false, repo_state: "missing" }),
    ]);
    renderPage();
    expect(await screen.findByText("5 tickets, 2 blocked")).toBeInTheDocument();
    expect(screen.getByText("1 ticket")).toBeInTheDocument();
    expect(screen.getByText("repo missing")).toBeInTheDocument();
  });

  it("archives after confirming, then lists it under Archived and can restore it", async () => {
    mockClient.archiveWorkspace.mockResolvedValue(summary("blog", "Blog", { archived_at: "2026-09-30T00:00:00Z" }));
    mockClient.restoreWorkspace.mockResolvedValue(summary("blog", "Blog"));
    renderPage();
    const archive = await screen.findByRole("button", { name: "Archive Blog" });

    (window.confirm as jest.Mock).mockReturnValueOnce(false);
    fireEvent.click(archive);
    expect(mockClient.archiveWorkspace).not.toHaveBeenCalled();

    mockClient.workspaces.mockResolvedValue([
      summary("shop", "Shop"),
      summary("blog", "Blog", { archived_at: "2026-09-30T00:00:00Z" }),
    ]);
    fireEvent.click(archive);
    await waitFor(() => expect(mockClient.archiveWorkspace).toHaveBeenCalledWith("blog"));

    const archived = (await screen.findByText("Archived")).closest("details") as HTMLElement;
    const restore = await within(archived).findByRole("button", { name: "Restore Blog" });
    expect(screen.getByRole("heading", { name: /Active workspaces/ })).toHaveTextContent("1");

    mockClient.workspaces.mockResolvedValue([summary("shop", "Shop"), summary("blog", "Blog")]);
    fireEvent.click(restore);
    await waitFor(() => expect(mockClient.restoreWorkspace).toHaveBeenCalledWith("blog"));
    await waitFor(() => expect(screen.queryByText("Archived")).not.toBeInTheDocument());
  });

  it("reports a failed archive and leaves the workspace active", async () => {
    mockClient.archiveWorkspace.mockRejectedValue(new Error("database is locked"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Archive Blog" }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts).toContainEqual(
        expect.objectContaining({ tone: "error", title: "Archive workspace failed", message: "database is locked" }),
      ),
    );
    expect(screen.getByRole("button", { name: "Archive Blog" })).toBeEnabled();
    expect(screen.queryByText("Archived")).not.toBeInTheDocument();
  });

  it("says when every workspace is archived, apart from having none", async () => {
    mockClient.workspaces.mockResolvedValue([
      summary("shop", "Shop", { archived_at: "2026-09-30T00:00:00Z" }),
      summary("blog", "Blog", { archived_at: "2026-09-30T00:00:00Z" }),
    ]);
    renderPage();
    expect(await screen.findByText(/Every workspace is archived/)).toBeInTheDocument();
  });

  it("says what to do when there are no workspaces", async () => {
    mockApi.workspaceTemplates.mockResolvedValue([]);
    mockClient.workspaces.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/No workspaces yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add workspace" })).toBeEnabled();
  });

  it("says the list failed to load, and retries", async () => {
    mockClient.workspaces.mockRejectedValueOnce(new Error("server unreachable"));
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("server unreachable");
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("adds a workspace from the tab and lists it", async () => {
    mockClient.workflowTemplates.mockResolvedValue([
      { slug: "loregarden-tdd", name: "TDD", description: "", stage_count: 6 } as never,
    ]);
    mockClient.createWorkspace.mockResolvedValue({
      id: "id-docs",
      slug: "docs",
      name: "Docs",
      workflow_template_slug: "loregarden-tdd",
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Add workspace" }));
    const dialog = await screen.findByRole("dialog", { name: "Add workspace" });
    await waitFor(() => expect(within(dialog).getByLabelText("Workflow template")).toBeEnabled());
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Docs" } });
    fireEvent.change(within(dialog).getByLabelText("Repo path"), { target: { value: "/w/docs" } });

    mockApi.workspaceTemplates.mockResolvedValue([workspace(), workspace({ slug: "docs", name: "Docs", entries: [] })]);
    mockClient.workspaces.mockResolvedValue([summary("shop", "Shop"), summary("docs", "Docs")]);
    fireEvent.click(await within(dialog).findByRole("button", { name: "Create workspace" }));

    await waitFor(() =>
      expect(mockClient.createWorkspace).toHaveBeenCalledWith(
        expect.objectContaining({ slug: "docs", name: "Docs", repo_path: "/w/docs", workflow_template_slug: "loregarden-tdd" }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("heading", { name: "Docs setup" })).toBeInTheDocument();
  });

  it("keeps the dialog open with the server's refusal", async () => {
    mockClient.createWorkspace.mockRejectedValue(new Error("slug already exists"));
    renderPage();
    const dialog = await openAddDialog("/w/docs");
    fireEvent.click(await within(dialog).findByRole("button", { name: "Create workspace" }));
    expect(await within(dialog).findByText("slug already exists")).toBeInTheDocument();
  });

  it("creates the repository too when nothing is at the path", async () => {
    probeAs("missing");
    mockClient.createWorkspace.mockResolvedValue({ id: "id-docs", slug: "docs", name: "Docs", workflow_template_slug: "loregarden-tdd" });
    mockClient.createWorkspaceRepository.mockResolvedValue({ ...summary("docs", "Docs"), follow_up: "" });
    renderPage();
    const dialog = await openAddDialog("/w/docs");
    expect(await within(dialog).findByText(/a new git repository will be created/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Create workspace and repository" }));
    await waitFor(() => expect(mockClient.createWorkspaceRepository).toHaveBeenCalledWith("docs"));
    expect(mockClient.createWorkspace).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("registers an existing repository without creating one", async () => {
    mockClient.createWorkspace.mockResolvedValue({ id: "id-docs", slug: "docs", name: "Docs", workflow_template_slug: "loregarden-tdd" });
    renderPage();
    const dialog = await openAddDialog("/w/docs");
    fireEvent.click(await within(dialog).findByRole("button", { name: "Create workspace" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(mockClient.createWorkspaceRepository).not.toHaveBeenCalled();
  });

  it("refuses a path that cannot be used, and says why", async () => {
    probeAs("not_a_repository", "/w/docs has files but is not a git repository");
    renderPage();
    const dialog = await openAddDialog("/w/docs");
    expect(await within(dialog).findByText(/has files but is not a git repository/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Create workspace" })).toBeDisabled();
  });

  it("asks for an absolute path rather than resolving a relative one", async () => {
    renderPage();
    const dialog = await openAddDialog("docs");
    expect(within(dialog).getByText(/Enter an absolute path/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Create workspace" })).toBeDisabled();
    expect(mockClient.probeRepository).not.toHaveBeenCalled();
  });

  it("closes and reports it when the workspace was added but its repository was not", async () => {
    useToastStore.setState({ toasts: [] });
    probeAs("missing");
    mockClient.createWorkspace.mockResolvedValue({ id: "id-docs", slug: "docs", name: "Docs", workflow_template_slug: "loregarden-tdd" });
    mockClient.createWorkspaceRepository.mockRejectedValue(new Error("git commit failed"));
    renderPage();
    const dialog = await openAddDialog("/w/docs");
    fireEvent.click(await within(dialog).findByRole("button", { name: "Create workspace and repository" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(useToastStore.getState().toasts).toContainEqual(
      expect.objectContaining({ tone: "error", message: "git commit failed" }),
    );
  });

  it("offers to create a missing repository from the card, after confirming", async () => {
    mockClient.workspaces.mockResolvedValue([
      summary("shop", "Shop"),
      summary("blog", "Blog", { repo_exists: false, repo_state: "missing" }),
    ]);
    let finish: () => void = () => {};
    mockClient.createWorkspaceRepository.mockImplementation(
      () => new Promise((resolve) => (finish = () => resolve({ ...summary("blog", "Blog"), follow_up: "" }))),
    );
    renderPage();
    const create = await screen.findByRole("button", { name: "Create repository" });
    (window.confirm as jest.Mock).mockReturnValueOnce(false);
    fireEvent.click(create);
    expect(mockClient.createWorkspaceRepository).not.toHaveBeenCalled();
    fireEvent.click(create);
    expect(await screen.findByRole("button", { name: "Creating repository…" })).toBeDisabled();
    expect(mockClient.createWorkspaceRepository).toHaveBeenCalledTimes(1);
    expect(mockClient.createWorkspaceRepository).toHaveBeenCalledWith("blog");
    finish();
    await waitFor(() => expect(mockClient.workspaces.mock.calls.length).toBeGreaterThan(1));
  });

  it("adds the ticked toolchain gates to the new workspace's profile, keeping its guardrails", async () => {
    mockClient.createWorkspace.mockResolvedValue({ id: "id-docs", slug: "docs", name: "Docs", workflow_template_slug: "loregarden-tdd" });
    renderPage();
    const dialog = await openAddDialog("/w/docs");
    const ruff = await within(dialog).findByRole("checkbox", { name: /Ruff lint/ });
    expect(ruff).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: /pytest/ })).not.toBeChecked();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /clippy/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Create workspace" }));
    await waitFor(() =>
      expect(mockClient.updateWorkspaceGates).toHaveBeenCalledWith("docs", {
        enabled: true,
        commands: [GUARDRAIL, RUFF, CARGO],
        transition_script: "ci/gates.py",
      }),
    );
  });

  it("saves no gates when none are ticked", async () => {
    mockClient.createWorkspace.mockResolvedValue({ id: "id-docs", slug: "docs", name: "Docs", workflow_template_slug: "loregarden-tdd" });
    renderPage();
    const dialog = await openAddDialog("/w/docs");
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: /Ruff lint/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Create workspace" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(mockClient.updateWorkspaceGates).not.toHaveBeenCalled();
  });

  it("offers every toolchain open when nothing is detected at a new path", async () => {
    probeAs("missing");
    mockClient.gatePresetsForPath.mockResolvedValue({
      ...PRESETS,
      toolchains: PRESETS.toolchains.map((t) => ({ ...t, detected: false, commands: t.commands.map((c) => ({ ...c, default_on: false })) })),
    });
    renderPage();
    const dialog = await openAddDialog("/w/docs");
    expect(await within(dialog).findByText(/Nothing detected here yet/)).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: /Ruff lint/ })).toBeVisible();
    expect(within(dialog).getByRole("checkbox", { name: /Ruff lint/ })).not.toBeChecked();
  });

  it("explains a folder it will not initialize, without offering to", async () => {
    mockClient.workspaces.mockResolvedValue([
      summary("shop", "Shop"),
      summary("blog", "Blog", { repo_state: "not_a_repository" }),
    ]);
    renderPage();
    expect(await screen.findByText("not a git repo")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create repository" })).not.toBeInTheDocument();
  });
});
