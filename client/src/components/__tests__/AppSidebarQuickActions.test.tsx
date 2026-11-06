/**
 * Right-clicking a sidebar page opens its quick actions (Memory → Add Reference).
 *
 * The add form itself is the reference library's own, covered in
 * `ReferenceLibraryTab.test.tsx`; here the claims are the menu's: it opens only
 * on pages that have actions, Add Reference opens that form over the current
 * page without navigating, a link action goes to its URL, and the menu can be
 * opened, operated and dismissed from the keyboard.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { AppSidebar } from "../AppSidebar";
import { api } from "../../api/client";
import type { LibraryRow } from "../../api/referenceLibraryApi";
import { memoryAutoRereviewPath } from "../../lib/appNavigation";
import { fetchSidebarEntries, fetchViews } from "../../lib/viewsApi";

jest.mock("../../api/client");
const mockApi = api as jest.Mocked<typeof api>;

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchViews: jest.fn(),
  fetchSidebarEntries: jest.fn(),
}));

const mockFetchViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockFetchEntries = fetchSidebarEntries as jest.MockedFunction<typeof fetchSidebarEntries>;

function Location() {
  const location = useLocation();
  return <span data-testid="location">{`${location.pathname}${location.search}`}</span>;
}

function renderSidebar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/"]}>
        <AppSidebar workspaceSlug="loregarden" onOpenSettings={jest.fn()} />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockFetchEntries.mockResolvedValue([]);
  mockFetchViews.mockResolvedValue([]);
  mockApi.workspaces.mockResolvedValue([
    { id: "w1", slug: "loregarden", name: "loregarden", archived_at: null },
  ] as Awaited<ReturnType<typeof api.workspaces>>);
  mockApi.initiatives.mockResolvedValue([]);
  mockApi.referenceTopics.mockResolvedValue([]);
});

test("right-clicking Memory offers Add Reference, which opens the add form over the current page", async () => {
  renderSidebar();
  const memory = await screen.findByRole("link", { name: "Memory" });

  const event = fireEvent.contextMenu(memory, { clientX: 40, clientY: 200 });
  // The browser's own menu would cover ours.
  expect(event).toBe(false);

  const menu = screen.getByRole("menu", { name: "Memory quick actions" });
  const item = screen.getByRole("menuitem", { name: "Add Reference…" });
  expect(menu).toContainElement(item);
  await waitFor(() => expect(item).toHaveFocus());

  fireEvent.click(item);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  const form = await screen.findByRole("form", { name: "Add a reference" });
  // The operator stays where they were.
  expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);

  fireEvent.keyDown(within(form).getByRole("textbox", { name: "URL" }), { key: "Escape" });
  expect(screen.queryByRole("form", { name: "Add a reference" })).not.toBeInTheDocument();
});

test("adding a reference from the quick action closes the form and stays on the page", async () => {
  mockApi.addReferenceSource.mockResolvedValue({
    source: { id: "new1", name: "retry-kit" } as LibraryRow,
    attached: ["loregarden"],
    scoped_pass: null,
    initiatives_linked: [],
  });
  renderSidebar();
  fireEvent.contextMenu(await screen.findByRole("link", { name: "Memory" }), { clientX: 40, clientY: 200 });
  fireEvent.click(screen.getByRole("menuitem", { name: "Add Reference…" }));
  const form = await screen.findByRole("form", { name: "Add a reference" });

  fireEvent.change(within(form).getByRole("textbox", { name: "URL" }), {
    target: { value: "https://github.com/acme/retry-kit" },
  });
  fireEvent.change(within(form).getByRole("textbox", { name: /What should the survey answer from it\?/ }), {
    target: { value: "How do they retry?" },
  });
  fireEvent.change(within(form).getByRole("textbox", { name: /Why this one\?/ }), { target: { value: "A retry ledger" } });
  await waitFor(() => expect(within(form).getByRole("button", { name: "Add to queue" })).toBeEnabled());
  fireEvent.click(within(form).getByRole("button", { name: "Add to queue" }));

  await waitFor(() => expect(screen.queryByRole("form", { name: "Add a reference" })).not.toBeInTheDocument());
  expect(mockApi.addReferenceSource).toHaveBeenCalledWith(
    expect.objectContaining({ url: "https://github.com/acme/retry-kit", workspace_slugs: ["loregarden"] }),
  );
  expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);
});

test("Memory also offers Auto Re-review, which opens the library's auto re-review settings", async () => {
  renderSidebar();
  fireEvent.contextMenu(await screen.findByRole("link", { name: "Memory" }), { clientX: 40, clientY: 200 });
  fireEvent.click(screen.getByRole("menuitem", { name: "Auto Re-review…" }));
  expect(screen.getByTestId("location")).toHaveTextContent(memoryAutoRereviewPath());
});

test("a page with no quick actions keeps the browser's menu", async () => {
  renderSidebar();
  const home = await screen.findByRole("link", { name: "Home" });

  expect(fireEvent.contextMenu(home)).toBe(true);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

test("Escape closes the menu and returns focus to the row", async () => {
  renderSidebar();
  const memory = await screen.findByRole("link", { name: "Memory" });
  memory.focus();

  // The ContextMenu key and Shift+F10 fire contextmenu with no pointer position.
  fireEvent.contextMenu(memory, { clientX: 0, clientY: 0 });
  const item = screen.getByRole("menuitem", { name: "Add Reference…" });
  await waitFor(() => expect(item).toHaveFocus());

  fireEvent.keyDown(item, { key: "Escape" });
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(memory).toHaveFocus();
  expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);
});

test("a press outside the menu closes it without navigating", async () => {
  renderSidebar();
  const memory = await screen.findByRole("link", { name: "Memory" });

  fireEvent.contextMenu(memory, { clientX: 40, clientY: 200 });
  expect(screen.getByRole("menu")).toBeInTheDocument();

  fireEvent.mouseDown(document.body);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);
});
