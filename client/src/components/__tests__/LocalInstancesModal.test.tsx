import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { LocalInstance, LocalInstanceTemplate } from "../../api/localInstancesTypes";
import { localInstancesApi } from "../../api/localInstancesApi";
import { useToastStore } from "../../state/toastStore";
import { LocalInstancesModal } from "../LocalInstancesModal";

jest.mock("../../api/localInstancesApi", () => ({
  localInstancesApi: {
    list: jest.fn(),
    templates: jest.fn(),
    launch: jest.fn(),
    logs: jest.fn(),
    stop: jest.fn(),
  },
}));

const mockApi = localInstancesApi as jest.Mocked<typeof localInstancesApi>;

const instance = (overrides: Partial<LocalInstance> = {}): LocalInstance => ({
  id: "server-feat-x-a1b2c3",
  project: "loregarden",
  name: "server-feat-x",
  kind: "server",
  role: "branch",
  template: "server",
  managed: true,
  url: "http://127.0.0.1:8101",
  health_path: "/health",
  ready_timeout_seconds: 300,
  log_path: "/r/logs/x.log",
  target_instance_id: null,
  labels: { branch: "feat-x" },
  started_at: "2026-09-26T00:00:00Z",
  exit_code: null,
  last_error: null,
  state: "ready",
  ...overrides,
});

const TEMPLATES: LocalInstanceTemplate[] = [
  {
    name: "client",
    kind: "client",
    description: "A dev client",
    params: [
      { key: "worktree", label: "Worktree", description: "", required: true, default: "/w/main", choices: ["/w/main", "/w/feat-x"] },
      { key: "target", label: "Server", description: "", required: false, default: "main", choices: ["main"] },
    ],
  },
];

function renderModal() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LocalInstancesModal open onClose={jest.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.getState().clear();
  mockApi.templates.mockResolvedValue(TEMPLATES);
});

it("shows a skeleton, not the empty state, while the first list loads", () => {
  mockApi.list.mockReturnValue(new Promise(() => {}));
  renderModal();
  expect(screen.getByLabelText("Loading instances")).toBeInTheDocument();
  expect(screen.queryByText(/Nothing is registered/)).not.toBeInTheDocument();
});

it("says how to get an instance when none is registered", async () => {
  mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
  renderModal();
  expect(await screen.findByText(/Nothing is registered/)).toBeInTheDocument();
});

it("reports a failed load rather than an empty list", async () => {
  mockApi.list.mockRejectedValue(new Error("connection refused"));
  renderModal();
  expect(await screen.findByText(/Could not load instances: connection refused/)).toBeInTheDocument();
  expect(screen.queryByText(/Nothing is registered/)).not.toBeInTheDocument();
});

it("keeps a crashed instance visible with its error, offering dismiss", async () => {
  mockApi.list.mockResolvedValue({
    instances: [instance({ state: "exited", last_error: "exited with code 1" })],
    unreadable: [],
  });
  renderModal();
  expect(await screen.findByText("exited with code 1")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
});

it("offers no stop for main, which registered itself", async () => {
  mockApi.list.mockResolvedValue({
    instances: [instance({ id: "loregarden-main", name: "main", role: "main", managed: false })],
    unreadable: [],
  });
  renderModal();
  expect(await screen.findByText("Not managed here")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
});

it("disables stop while it is in flight and toasts a failure", async () => {
  mockApi.list.mockResolvedValue({ instances: [instance()], unreadable: [] });
  let reject: (error: Error) => void = () => {};
  mockApi.stop.mockReturnValue(new Promise((_, r) => (reject = r)));
  renderModal();
  fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
  const busy = screen.getByRole("button", { name: "Stopping…" });
  expect(busy).toBeDisabled();
  fireEvent.click(busy);
  expect(mockApi.stop).toHaveBeenCalledTimes(1);
  await act(async () => reject(new Error("permission denied")));
  await waitFor(() =>
    expect(useToastStore.getState().toasts.map((t) => t.message)).toContain("permission denied"),
  );
});

it("launches with the template's defaults", async () => {
  mockApi.list.mockResolvedValue({ instances: [], unreadable: [] });
  mockApi.launch.mockResolvedValue(instance({ kind: "client", name: "client-main" }));
  renderModal();
  const form = await screen.findByRole("form", { name: "Launch an instance" });
  fireEvent.click(within(form).getByRole("button", { name: "Launch" }));
  await waitFor(() =>
    expect(mockApi.launch).toHaveBeenCalledWith({
      template: "client",
      name: undefined,
      params: { worktree: "/w/main", target: "main" },
    }),
  );
});

it("names the server a client proxies to", async () => {
  const server = instance();
  mockApi.list.mockResolvedValue({
    instances: [server, instance({ id: "client-1", name: "ui", kind: "client", target_instance_id: server.id })],
    unreadable: [],
  });
  renderModal();
  expect(await screen.findByText(/branch client → server-feat-x/)).toBeInTheDocument();
});

it("opens the log from a toggle a keyboard can reach", async () => {
  mockApi.list.mockResolvedValue({ instances: [instance()], unreadable: [] });
  mockApi.logs.mockResolvedValue({ path: "/x", lines: ["Uvicorn running"], truncated: false });
  renderModal();
  const toggle = await screen.findByRole("button", { name: "Log" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(toggle);
  expect(await screen.findByLabelText(`Log for ${instance().id}`)).toHaveTextContent("Uvicorn running");
});
