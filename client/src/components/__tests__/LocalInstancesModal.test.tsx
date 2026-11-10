import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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
  const onClose = jest.fn();
  return {
    onClose,
    ...render(
      <QueryClientProvider client={client}>
        <LocalInstancesModal open onClose={onClose} />
      </QueryClientProvider>,
    ),
  };
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
  // lg-milestone-that-2215 AC7: the name is the instance's own name and kind,
  // not its id — `id` carries a `secrets.token_hex(3)` suffix a screen reader
  // spells out letter by letter.
  expect(await screen.findByLabelText("Log for server-feat-x server")).toHaveTextContent("Uvicorn running");
});

/**
 * lg-milestone-that-2215 — `.local-instances-log` is the clearest instance of
 * this ticket's shape in the tree: a `max-height: 240px` `<pre>` with
 * `overflow: auto`, tabbable and aria-labelled, and with NO focus ring in any
 * stylesheet (`grep focus-visible LocalInstancesModal.css` returns nothing).
 * So the keyboard can land on it and nothing on screen says so.
 *
 * Four changes, and each has an assertion below:
 *
 * 1. The shared `scroll-focus-ring` class, defined once in `index.css` (AC2).
 * 2. `role="group"`, because `<pre>` maps to no ARIA role and an `aria-label`
 *    on a generic element is not reliably exposed (AC7).
 * 3. The accessible name becomes `Log for ${name} ${kind}` — "Log for
 *    server-feat-x server". Today it is `Log for ${id}`, and `id` is
 *    `slugify(f"{label}-{secrets.token_hex(3)}")`, so a screen reader spells
 *    the hex suffix out letter by letter. The field is `kind`, NOT `role`:
 *    `role` is `"main" | "branch"` and would announce "server-feat-x branch",
 *    which separates nothing an operator asked about (AC7).
 * 4. Nothing else. No key handler, no programmatic focus, and Escape still
 *    belongs to the modal (AC9).
 */
describe("the instance log region", () => {
  const LOG = { path: "/x", lines: ["Uvicorn running", "Application startup complete"], truncated: false };
  const NAME = "Log for server-feat-x server";

  /** Open the row's log and hand back the region. The log is gated behind it. */
  async function openLog(logs: Partial<Parameters<typeof mockApi.logs.mockResolvedValue>[0]> = LOG) {
    mockApi.list.mockResolvedValue({ instances: [instance()], unreadable: [] });
    mockApi.logs.mockResolvedValue(logs as typeof LOG);
    const view = renderModal();
    fireEvent.click(await screen.findByRole("button", { name: "Log" }));
    return view;
  }

  it("exposes a group with a name an operator can hear, not the id's hex suffix", async () => {
    await openLog();

    const region = await screen.findByRole("group", { name: NAME });
    expect(region.tagName).toBe("PRE");
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveClass("scroll-focus-ring");
    expect(region).toHaveTextContent("Uvicorn running");
    // The old name named the instance by its id, which is what this replaces.
    expect(screen.queryByLabelText(`Log for ${instance().id}`)).toBeNull();
  });

  it("names the server and the client apart, which is what the second field is for", async () => {
    // Two instances may share one name — `InstanceRecord`'s own docstring says
    // `name` is "unique only within intent" — so the name carries `kind`.
    mockApi.list.mockResolvedValue({
      instances: [instance({ id: "c1", kind: "client", log_path: "/r/logs/c.log" })],
      unreadable: [],
    });
    mockApi.logs.mockResolvedValue(LOG);
    renderModal();
    fireEvent.click(await screen.findByRole("button", { name: "Log" }));

    expect(await screen.findByRole("group", { name: "Log for server-feat-x client" })).toBeInTheDocument();
  });

  it("gives back the row's overflow menu on Shift+Tab, not Stop", async () => {
    // AC3, directional: every control that acts on this row precedes the log
    // in DOM order, so there is no forward stop to assert. The LAST of them is
    // the ⋯ trigger in `.local-instances-row-actions`, after Log and Stop —
    // asserting Stop here would pass only by accident of ordering.
    await openLog();
    const region = await screen.findByRole("group", { name: NAME });
    region.focus();
    expect(document.activeElement).toBe(region);

    await userEvent.tab({ shift: true });

    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "More actions for server-feat-x" }),
    );
  });

  it("renders one line of text in its place while the log is loading", async () => {
    // AC8, the loading case. No skeleton and no spinner: the region's content
    // height is unknown and the wait is one request.
    mockApi.list.mockResolvedValue({ instances: [instance()], unreadable: [] });
    mockApi.logs.mockReturnValue(new Promise(() => {}));
    renderModal();
    fireEvent.click(await screen.findByRole("button", { name: "Log" }));

    expect(await screen.findByText("Loading log…")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: NAME })).toBeNull();
  });

  it("renders no labelled box when the log is empty", async () => {
    // AC8. A labelled box at zero rows is a dead tab stop, and it reads
    // exactly like the failure state.
    await openLog({ path: "/x", lines: [], truncated: false });

    expect(
      await screen.findByText("The log is empty — the process has written nothing yet."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: NAME })).toBeNull();
  });

  it("says why the log could not be read, in its place and not as a toast", async () => {
    // AC8, the error case: a `role="alert"` paragraph that STAYS, because a
    // toast leaves the operator looking at a box with nothing in it.
    mockApi.list.mockResolvedValue({ instances: [instance()], unreadable: [] });
    mockApi.logs.mockRejectedValue(new Error("permission denied"));
    renderModal();
    fireEvent.click(await screen.findByRole("button", { name: "Log" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/permission denied/);
    expect(screen.queryByRole("group", { name: NAME })).toBeNull();
  });

  it("binds no key of its own", async () => {
    // AC9. The keys that scroll an overflow container are the browser's, and
    // the region competes with none of them.
    await openLog();
    const region = await screen.findByRole("group", { name: NAME });
    region.focus();

    for (const key of ["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End", " "]) {
      const event = createEvent.keyDown(region, { key, bubbles: true });
      fireEvent(region, event);
      expect(event.defaultPrevented).toBe(false);
      expect(document.activeElement).toBe(region);
    }
  });

  it("lets Escape from inside it close the whole modal", async () => {
    // AC9, and the reason it is a criterion: this region renders inside
    // `ModalShell`, so a region that consumed Escape would break
    // `useDialogDismiss` for every control in the dialog.
    const { onClose } = await openLog();
    const region = await screen.findByRole("group", { name: NAME });
    region.focus();

    fireEvent.keyDown(region, { key: "Escape", bubbles: true });

    expect(onClose).toHaveBeenCalled();
  });

  it("is never focused for the operator when the log arrives", async () => {
    // AC9. Opening the disclosure must not move focus into the region; the
    // operator's own Tab is the only way in. The toggle keeps focus, which is
    // where `aria-expanded` is read from.
    await openLog();
    const toggle = screen.getByRole("button", { name: "Hide log" });
    toggle.focus();

    const region = await screen.findByRole("group", { name: NAME });

    expect(document.activeElement).toBe(toggle);
    expect(document.activeElement).not.toBe(region);
  });
});
