/**
 * AC3 — "the page error boundary resets per view; a crash inside one view does
 * not persist after navigating to a different view."
 *
 * `PageErrorBoundary` lives in `App.tsx` and is keyed on
 * `pageFromPath(location.pathname)`. That function answers `"home"` for every
 * path it does not recognise, so today **every** view route and the Home page
 * share one key: a container that throws in view A keeps its fallback on screen
 * in view B, and on `/` — the page whose whole job is to be the way out.
 *
 * These tests assert that behaviour rather than the key string. A key is an
 * implementation detail with several defensible spellings; "the crash is gone
 * when I get to the other view" is the thing the user is promised, and it is
 * the thing that is broken.
 *
 * Two guards keep the suite honest. `resetKey={Math.random()}` clears the error
 * on every render and would pass every navigation case here, so one test
 * re-renders the shell *without* navigating and requires the fallback to
 * survive. And the crash has to be reachable at all: the route table's
 * catch-all currently sends `/view/anything` to `/`, so the first assertion
 * that a view route renders its host is itself part of AC1.
 *
 * `AppShell` is not exported today. Exporting it is the seam these tests need:
 * `App` builds its own `BrowserRouter`, and a boundary-reset test has to drive
 * navigation, which means `MemoryRouter`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { AppShell } from "../../App";
import { RouterBridgeSync } from "../RouterBridgeSync";

/** The message a crashing view throws; `PageErrorBoundary` renders it verbatim. */
const CRASH = "kaboom-from-the-view";

/**
 * The layout is chrome — a topbar, the dock, the sidebar and its socket. None
 * of it is the subject here, and the nav links the tests drive have to live
 * inside the router, which this is.
 */
jest.mock("../AppLayout", () => {
  const { Link: RouterLink } = jest.requireActual("react-router-dom");
  const { useState: useReactState } = jest.requireActual("react");
  return {
    AppLayout: ({ children }: { children: ReactNode }) => {
      const [tick, setTick] = useReactState(0);
      return (
        <div>
          <button type="button" onClick={() => setTick(tick + 1)}>
            force rerender {tick}
          </button>
          <RouterLink to="/view/ok">go to ok view</RouterLink>
          <RouterLink to="/view/other">go to other view</RouterLink>
          <RouterLink to="/view/boom">go to boom view</RouterLink>
          <RouterLink to="/">go home</RouterLink>
          {children}
        </div>
      );
    },
  };
});

jest.mock("../../state/QueueStatusContext", () => ({
  QueueStatusProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

jest.mock("../../pages/ViewPage", () => {
  const { useParams } = jest.requireActual("react-router-dom");
  return {
    ViewPage: () => {
      const { viewId } = useParams();
      if (viewId === "boom") throw new Error("kaboom-from-the-view");
      return <div data-testid="view-host">view {viewId}</div>;
    },
  };
});

jest.mock("../../pages/HomePage", () => ({
  HomePage: () => <div data-testid="home-page">Home</div>,
}));

// The rest of the route table is irrelevant here and expensive to mount.
jest.mock("../../pages/Dashboard", () => ({ Dashboard: () => null }));
jest.mock("../../pages/StudioPage", () => ({ StudioPage: () => null }));
jest.mock("../../pages/EditorPage", () => ({ EditorPage: () => null }));
jest.mock("../../pages/QueuePage", () => ({ QueuePage: () => null }));
jest.mock("../../pages/BaxterChatPage", () => ({ BaxterChatPage: () => null }));
jest.mock("../../pages/BranchTriagePage", () => ({ BranchTriagePage: () => null }));
jest.mock("../../pages/McpGatewayPage", () => ({ McpGatewayPage: () => null }));
jest.mock("../TicketTabRedirect", () => ({ TicketTabRedirect: () => null }));
jest.mock("../StudioSectionRedirect", () => ({ StudioSectionRedirect: () => null }));

function renderShell(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <RouterBridgeSync />
        <AppShell />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The boundary's fallback, identified by the error message it renders. */
function crashed(): boolean {
  return screen.queryByText(CRASH) !== null;
}

let consoleError: jest.SpyInstance;

beforeEach(() => {
  // React logs every caught error; the boundary already re-logs it, and three
  // stack traces per test hide real failures.
  consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  consoleError.mockRestore();
});

describe("AC1 — the view route exists and resolves its id", () => {
  it("renders the view host for /view/:viewId", async () => {
    const user = userEvent.setup();
    renderShell("/");

    await user.click(screen.getByRole("link", { name: "go to ok view" }));

    // Without the route, the catch-all sends this straight back to "/".
    expect(await screen.findByTestId("view-host")).toHaveTextContent("view ok");
  });
});

describe("AC3 — a crash in one view does not follow the user out of it", () => {
  it("clears when navigating to a different view", async () => {
    const user = userEvent.setup();
    renderShell("/view/boom");

    await waitFor(() => expect(crashed()).toBe(true));

    await user.click(screen.getByRole("link", { name: "go to other view" }));

    await waitFor(() => expect(screen.getByTestId("view-host")).toHaveTextContent("view other"));
    expect(crashed()).toBe(false);
  });

  it("clears when navigating Home", async () => {
    // The worst case of the shared key: the fallback's own "Back to Home"
    // button lands on a Home page that is still showing the fallback.
    const user = userEvent.setup();
    renderShell("/view/boom");
    await waitFor(() => expect(crashed()).toBe(true));

    await user.click(screen.getByRole("link", { name: "go home" }));

    await waitFor(() => expect(screen.getByTestId("home-page")).toBeInTheDocument());
    expect(crashed()).toBe(false);
  });

  it("clears when arriving at a view from Home", async () => {
    const user = userEvent.setup();
    renderShell("/view/boom");
    await waitFor(() => expect(crashed()).toBe(true));

    await user.click(screen.getByRole("link", { name: "go home" }));
    await screen.findByTestId("home-page");
    await user.click(screen.getByRole("link", { name: "go to ok view" }));

    await waitFor(() => expect(screen.getByTestId("view-host")).toHaveTextContent("view ok"));
    expect(crashed()).toBe(false);
  });

  it("does not clear on a re-render that is not a navigation", async () => {
    // The guard against a reset key that is fresh every render — which would
    // satisfy every test above while making the boundary useless: the crashing
    // view would be re-mounted and re-throw on any parent state change,
    // flickering between the fallback and the crash.
    const user = userEvent.setup();
    renderShell("/view/boom");
    await waitFor(() => expect(crashed()).toBe(true));

    await user.click(screen.getByRole("button", { name: /force rerender/i }));

    expect(crashed()).toBe(true);
  });

  it("keeps showing the crash while the user stays on the broken view", async () => {
    const user = userEvent.setup();
    renderShell("/view/ok");
    await screen.findByTestId("view-host");

    await user.click(screen.getByRole("link", { name: "go to boom view" }));

    await waitFor(() => expect(crashed()).toBe(true));
    expect(screen.queryByTestId("view-host")).toBeNull();
  });

  it("still catches the next crash after it has reset once", async () => {
    // The other way to make every navigation case above pass while breaking the
    // boundary: reset, and stay reset. A boundary that clears its error and
    // then stops catching lets the second crash escape to the top of the tree,
    // which in the real app is a white screen with no way back.
    const user = userEvent.setup();
    renderShell("/view/boom");
    await waitFor(() => expect(crashed()).toBe(true));

    await user.click(screen.getByRole("link", { name: "go to ok view" }));
    await waitFor(() => expect(screen.getByTestId("view-host")).toHaveTextContent("view ok"));

    await user.click(screen.getByRole("link", { name: "go to boom view" }));

    await waitFor(() => expect(crashed()).toBe(true));
    // And the chrome around it is still mounted, so the user is not stranded.
    expect(screen.getByRole("link", { name: "go home" })).toBeInTheDocument();
  });

  it("does not reset for a non-view page whose key it already shared", async () => {
    // `pageFromPath` answers "home" for `/` *and* for every unrecognised path,
    // so a fix that only appends the view id — `${page}:${viewId ?? ""}` — is
    // right, while one that replaces the key with the view id alone collapses
    // every non-view page onto the empty string and re-breaks Home against the
    // other pages. Home is the page the fallback's own button lands on, so it
    // is the one worth spelling out.
    const user = userEvent.setup();
    renderShell("/view/boom");
    await waitFor(() => expect(crashed()).toBe(true));

    await user.click(screen.getByRole("link", { name: "go home" }));
    await screen.findByTestId("home-page");

    // Home renders, the fallback is gone, and the boundary is still armed.
    expect(crashed()).toBe(false);
    await user.click(screen.getByRole("link", { name: "go to boom view" }));
    await waitFor(() => expect(crashed()).toBe(true));
  });
});
