/**
 * Right-clicking a sidebar page opens its quick actions (Memory → Add Reference).
 *
 * The flow it leads to is the reference library's own add form, covered in
 * `ReferenceLibraryTab.test.tsx`; here the claims are the menu's: it opens only
 * on pages that have actions, choosing an item goes to the flow's URL, and it
 * can be opened, operated and dismissed from the keyboard.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { AppSidebar } from "../AppSidebar";
import { memoryAddReferencePath } from "../../lib/appNavigation";
import { fetchSidebarEntries, fetchViews } from "../../lib/viewsApi";

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
});

test("right-clicking Memory offers Add Reference, which opens the library's add form", async () => {
  renderSidebar();
  const memory = await screen.findByRole("link", { name: "Memory" });

  const event = fireEvent.contextMenu(memory, { clientX: 40, clientY: 200 });
  // The browser's own menu would cover ours.
  expect(event).toBe(false);

  const menu = screen.getByRole("menu", { name: "Memory quick actions" });
  const item = screen.getByRole("menuitem", { name: "Add Reference" });
  expect(menu).toContainElement(item);
  await waitFor(() => expect(item).toHaveFocus());

  fireEvent.click(item);
  expect(screen.getByTestId("location")).toHaveTextContent(memoryAddReferencePath());
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
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
  const item = screen.getByRole("menuitem", { name: "Add Reference" });
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
