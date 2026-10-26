import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { api } from "../../api/client";
import { ApiError } from "../../api/http";
import type { TicketSummary } from "../../api/types";
import { matchPages } from "../../lib/topbarSearch";
import { TopbarSearch } from "../TopbarSearch";

jest.mock("../../api/client", () => ({
  api: { tickets: jest.fn() },
}));

const mockTickets = api.tickets as jest.MockedFunction<typeof api.tickets>;

function ticket(overrides: Partial<TicketSummary>): TicketSummary {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    external_id: "lg-auth-7",
    title: "Auth token refresh",
    state: "in_progress",
    priority: 2,
    workspace_slug: "loregarden",
    workflow_stage_key: "",
    workflow_stage_status: "pending",
    workflow_stage_name: "",
    run_code: "",
    ...overrides,
  } as TicketSummary;
}

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function renderSearch() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/"]}>
        <TopbarSearch />
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return screen.getByRole("combobox", { name: "Search pages and tickets" });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockTickets.mockResolvedValue([]);
});

test("pages match by name, in rail order", () => {
  expect(matchPages("ma").map((page) => page.label)).toEqual(["Machine"]);
  expect(matchPages("  WORK ").map((page) => page.label)).toEqual(["Work items", "Workspaces"]);
  expect(matchPages("")).toEqual([]);
});

test("a page shows on the first keystroke, and Enter goes there", async () => {
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "q");
  const list = screen.getByRole("listbox", { name: "Search results" });
  expect(within(list).getByRole("option", { name: /Queue/ })).toHaveAttribute("aria-selected", "true");
  // One letter is not enough to search tickets.
  expect(mockTickets).not.toHaveBeenCalled();

  await user.keyboard("{Enter}");
  expect(screen.getByTestId("where")).toHaveTextContent("/queue");
  expect(box).toHaveValue("");
});

test("two letters search tickets by id or title, and Enter on one opens it", async () => {
  mockTickets.mockResolvedValue([ticket({})]);
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "auth");
  const option = await screen.findByRole("option", { name: /lg-auth-7/ });
  expect(mockTickets).toHaveBeenLastCalledWith({ search: "auth", limit: 8 });
  expect(option).toHaveTextContent("Auth token refresh");

  await user.keyboard("{ArrowDown}");
  expect(option).toHaveAttribute("aria-selected", "true");
  expect(box).toHaveAttribute("aria-activedescendant", option.id);
  await user.keyboard("{Enter}");
  expect(screen.getByTestId("where")).toHaveTextContent(
    "/tickets/11111111-1111-4111-8111-111111111111",
  );
});

test("a failed ticket search says so in the results, beside the pages that still match", async () => {
  mockTickets.mockRejectedValue(new ApiError(503, "search unavailable"));
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "wor");
  expect(await screen.findByText(/Couldn't search tickets/)).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /Work items/ })).toBeInTheDocument();
  expect(box).toHaveAttribute("aria-expanded", "true");
});

test("nothing matching says what search looks at", async () => {
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "zzqx");
  expect(await screen.findByText(/Nothing matches “zzqx”/)).toBeInTheDocument();
});

test("Escape clears the text, then leaves the field", async () => {
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "home");
  await user.keyboard("{Escape}");
  expect(box).toHaveValue("");
  expect(box).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(box).not.toHaveFocus();
});

test("⌘K or Ctrl+K focuses the box from anywhere on the page", () => {
  const box = renderSearch();

  act(() => {
    fireEvent.keyDown(window, { key: "k", metaKey: true });
  });
  expect(box).toHaveFocus();

  box.blur();
  act(() => {
    fireEvent.keyDown(window, { key: "K", ctrlKey: true });
  });
  expect(box).toHaveFocus();
});

test("⌘K is left to the code editor while it has focus", () => {
  const box = renderSearch();
  const editor = document.createElement("div");
  editor.className = "monaco-editor";
  const field = document.createElement("textarea");
  editor.appendChild(field);
  document.body.appendChild(editor);
  field.focus();

  fireEvent.keyDown(field, { key: "k", metaKey: true });
  expect(field).toHaveFocus();
  expect(box).not.toHaveFocus();
  editor.remove();
});
