/**
 * The labelled rail: docked open by default where the window has room, folded
 * to icons on request or on a narrow window, with live counts beside pages.
 *
 * jsdom has no layout, so "pushes the page over" is asserted as the structural
 * cause the CSS keys on (`data-docked` on the rail), and the 196px itself is a
 * browser check.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import type { TicketStatusSummary } from "../../api/types";
import { fetchSidebarEntries, fetchViews } from "../../lib/viewsApi";
import { useUiStore } from "../../state/uiStore";
import { AppSidebar, RAIL_DOCK_QUERY } from "../AppSidebar";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchViews: jest.fn(),
  fetchSidebarEntries: jest.fn(),
}));

const mockFetchViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockFetchEntries = fetchSidebarEntries as jest.MockedFunction<typeof fetchSidebarEntries>;

/** A window whose width does (or does not) leave room to dock the rail. */
function windowWide(wide: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: query === RAIL_DOCK_QUERY ? wide : false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  });
}

function summary(overrides: Partial<TicketStatusSummary> = {}): TicketStatusSummary {
  return {
    backlog: 0,
    in_progress: 0,
    blocked: 0,
    parked: 0,
    done: 0,
    wont_do: 0,
    running: 0,
    awaiting: 0,
    queued: 0,
    idle: 0,
    ...overrides,
  };
}

function renderSidebar(ticketSummary?: TicketStatusSummary) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AppSidebar
          workspaceSlug="loregarden"
          onOpenSettings={jest.fn()}
          ticketSummary={ticketSummary}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return screen.getByRole("navigation", { name: "Main navigation" });
}

beforeEach(() => {
  mockFetchViews.mockResolvedValue([]);
  mockFetchEntries.mockResolvedValue([]);
  useUiStore.setState({ railDocked: true });
});

afterEach(() => {
  // Leave jsdom as other suites expect it: no matchMedia at all.
  delete (window as { matchMedia?: unknown }).matchMedia;
});

test("a wide window opens the rail docked, with names showing before any hover", () => {
  windowWide(true);
  const nav = renderSidebar();

  expect(nav).toHaveAttribute("data-docked", "true");
  expect(nav).toHaveAttribute("data-expanded", "true");
  expect(screen.getByRole("button", { name: "Keep sidebar open" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

test("the pages are grouped under Work, Build and System", () => {
  windowWide(true);
  renderSidebar();

  expect(screen.getByRole("list", { name: "Work" })).toContainElement(
    screen.getByRole("link", { name: "Work items" }),
  );
  expect(screen.getByRole("list", { name: "Build" })).toContainElement(
    screen.getByRole("link", { name: "Branch Triage" }),
  );
  expect(screen.getByRole("list", { name: "System" })).toContainElement(
    screen.getByRole("link", { name: "Machine" }),
  );
});

test("folding the rail sticks, and opens it back to icons that widen on hover", async () => {
  windowWide(true);
  const user = userEvent.setup();
  const nav = renderSidebar();

  await user.click(screen.getByRole("button", { name: "Keep sidebar open" }));

  expect(useUiStore.getState().railDocked).toBe(false);
  expect(nav).toHaveAttribute("data-docked", "false");
  // A pointer press folds it at once, rather than waiting for the pointer to leave.
  expect(nav).toHaveAttribute("data-expanded", "false");
  expect(screen.getByRole("button", { name: "Keep sidebar open" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );

  await user.click(screen.getByRole("button", { name: "Keep sidebar open" }));
  expect(useUiStore.getState().railDocked).toBe(true);
  expect(nav).toHaveAttribute("data-docked", "true");
});

test("a keyboard press keeps focus on the toggle", async () => {
  windowWide(true);
  const user = userEvent.setup();
  renderSidebar();
  const toggle = screen.getByRole("button", { name: "Keep sidebar open" });

  toggle.focus();
  await user.keyboard("{Enter}");

  expect(useUiStore.getState().railDocked).toBe(false);
  expect(document.activeElement).toBe(toggle);
});

test("a narrow window folds the rail whatever the preference, and offers no toggle", () => {
  windowWide(false);
  const nav = renderSidebar();

  expect(useUiStore.getState().railDocked).toBe(true);
  expect(nav).toHaveAttribute("data-docked", "false");
  expect(nav).toHaveAttribute("data-expanded", "false");
  expect(screen.queryByRole("button", { name: "Keep sidebar open" })).not.toBeInTheDocument();
});

test("tickets waiting on a person show beside Work items, as its description", () => {
  windowWide(true);
  renderSidebar(summary({ parked: 2, awaiting: 19 }));

  const link = screen.getByRole("link", { name: "Work items" });
  expect(link).toHaveAccessibleDescription("21 need you");
  expect(link).toHaveTextContent("21");
  // On the icon's corner, outside what folds away with the labels.
  const badge = link.querySelector(".app-sidebar-icon .app-sidebar-page-count");
  expect(badge).toHaveTextContent("21");
  expect(badge).not.toHaveClass("app-sidebar-reveal");
});

test("with nobody waited on, Work items carries no count", () => {
  windowWide(true);
  renderSidebar(summary());

  const link = screen.getByRole("link", { name: "Work items" });
  expect(link).not.toHaveAttribute("aria-describedby");
});
