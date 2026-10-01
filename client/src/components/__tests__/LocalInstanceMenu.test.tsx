import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { LocalInstance } from "../../api/localInstancesTypes";
import { platform } from "../../services/platform";
import { useToastStore } from "../../state/toastStore";
import { LocalInstanceMenu } from "../LocalInstanceMenu";

jest.mock("../../services/platform", () => ({
  platform: { reusesTabs: true, openInTab: jest.fn(), openExternal: jest.fn() },
}));

const mockPlatform = platform as jest.Mocked<typeof platform> & { reusesTabs: boolean };

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
  ready_timeout_seconds: 120,
  log_path: null,
  target_instance_id: null,
  labels: {},
  started_at: "2026-09-30T00:00:00Z",
  exit_code: null,
  last_error: null,
  state: "ready",
  ...overrides,
});

const items = () => screen.getAllByRole("menuitem").map((item) => item.textContent);

function openMenu(of = instance()) {
  render(<LocalInstanceMenu instance={of} />);
  fireEvent.click(screen.getByRole("button", { name: `More actions for ${of.name}` }));
}

beforeEach(() => {
  jest.clearAllMocks();
  mockPlatform.reusesTabs = true;
  mockPlatform.openInTab.mockResolvedValue(undefined);
  mockPlatform.openExternal.mockResolvedValue(undefined);
  useToastStore.setState({ toasts: [] });
});

it("reopens an instance in its own tab every time, and a new tab on request", () => {
  openMenu();
  const trigger = screen.getByRole("button", { name: "More actions for server-feat-x" });
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in existing tab" }));
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in existing tab" }));
  expect(mockPlatform.openInTab).toHaveBeenCalledTimes(2);
  const [first, second] = mockPlatform.openInTab.mock.calls;
  expect(first).toEqual(["http://127.0.0.1:8101", expect.stringContaining("server-feat-x-a1b2c3")]);
  expect(second).toEqual(first);

  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in new tab" }));
  expect(mockPlatform.openExternal).toHaveBeenCalledWith("http://127.0.0.1:8101");
});

it("gives two instances different tabs", () => {
  render(
    <>
      <LocalInstanceMenu instance={instance()} />
      <LocalInstanceMenu instance={instance({ id: "client-1", name: "ui" })} />
    </>,
  );
  fireEvent.click(screen.getByRole("button", { name: "More actions for server-feat-x" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in existing tab" }));
  fireEvent.click(screen.getByRole("button", { name: "More actions for ui" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in existing tab" }));
  const [[, a], [, b]] = mockPlatform.openInTab.mock.calls;
  expect(a).not.toEqual(b);
});

it("offers one open in the desktop app, which cannot pick the browser's tab", () => {
  mockPlatform.reusesTabs = false;
  openMenu();
  expect(items()).toEqual(["Open in browser", "Copy URL"]);
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in browser" }));
  expect(mockPlatform.openExternal).toHaveBeenCalledWith("http://127.0.0.1:8101");
});

it("offers no open for an exited instance, whose port has nothing behind it", () => {
  openMenu(instance({ state: "exited" }));
  expect(items()).toEqual(["Copy URL"]);
});

it("says why an open failed", async () => {
  mockPlatform.openInTab.mockRejectedValue(new Error("The browser blocked the tab"));
  openMenu();
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in existing tab" }));
  await waitFor(() =>
    expect(useToastStore.getState().toasts).toContainEqual(
      expect.objectContaining({ tone: "error", title: "Open instance failed", message: "The browser blocked the tab" }),
    ),
  );
});
