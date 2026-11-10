/**
 * An agent version's role-text pane, and its keyboard path.
 *
 * lg-milestone-that-2215: `.studio-version-body` is a bounded `overflow: auto`
 * <pre> holding a whole stored role body — thousands of characters on every
 * real agent. The `View` button that opens it is in the row ABOVE it, so the
 * snapshot could not be scrolled from the keyboard at all.
 *
 * The loading and no-snapshot states used to render INSIDE the pane, as the
 * strings "Loading…" and "(no snapshot)", which is the labelled box over one
 * line that AC8 forbids. Each is now a line in the pane's place.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import { AgentVersionHistory } from "../AgentVersionHistory";
import { api } from "../../../api/client";
import type { StudioAgent, StudioAgentVersion } from "../../../api/studioTypes";

jest.mock("../../../api/client", () => ({
  api: {
    studioAgentVersions: jest.fn(),
    studioAgentVersion: jest.fn(),
    restoreStudioAgentVersion: jest.fn(),
  },
}));
const mockVersions = api.studioAgentVersions as jest.MockedFunction<typeof api.studioAgentVersions>;
const mockVersion = api.studioAgentVersion as jest.MockedFunction<typeof api.studioAgentVersion>;

const ROLE_BODY = ["# Planner", "", "You write plans.", "You never write code."].join("\n");

const VERSIONS: StudioAgentVersion[] = [
  { version: 3, created_by: "whjake", change_note: "Tighten the scope", created_at: "2026-11-01T10:00:00Z" },
  { version: 2, created_by: "whjake", change_note: "", created_at: "2026-10-28T10:00:00Z" },
];

function renderHistory() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<AgentVersionHistory slug="planner" currentVersion={3} />, { wrapper });
}

/** Open the history, then the named version. */
async function openVersion(version: number) {
  fireEvent.click(await screen.findByText(/Version history/));
  const rows = await screen.findAllByRole("button", { name: "View" });
  // Newest-first, so v3 is the first row.
  fireEvent.click(rows[VERSIONS.findIndex((v) => v.version === version)]);
}

const REGION = (version: number) => `Role text for v${version}`;

beforeEach(() => {
  jest.clearAllMocks();
  mockVersions.mockResolvedValue(VERSIONS);
});

describe("the stored role text's keyboard path", () => {
  it("is a tab stop that steps back to the View button that opened it — AC3", async () => {
    mockVersion.mockResolvedValue({
      ...VERSIONS[0],
      snapshot: { slug: "planner", role_body: ROLE_BODY } as unknown as StudioAgent,
    });
    renderHistory();
    await openVersion(3);

    const pane = await screen.findByRole("group", { name: REGION(3) });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");

    // The row's controls PRECEDE the pane, and `View` becomes `Hide` once the
    // pane is open — so that is the named control the keyboard steps back to.
    // v3 is the head version, so it has no `Restore` beside it.
    pane.focus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Hide" })).toHaveFocus();
  });

  it("puts a line in its place while loading and when nothing was stored — AC8", async () => {
    let settle: ((value: StudioAgentVersion) => void) | undefined;
    mockVersion.mockReturnValue(
      new Promise((resolve) => {
        settle = resolve;
      }),
    );
    renderHistory();
    await openVersion(3);

    expect(await screen.findByText("Loading the role text…")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: REGION(3) })).toBeNull();

    settle?.({ ...VERSIONS[0], snapshot: null });
    expect(await screen.findByText("No snapshot was stored for this version.")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: REGION(3) })).toBeNull();
  });
});
