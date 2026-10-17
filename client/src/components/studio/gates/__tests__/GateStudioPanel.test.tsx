import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ApiError } from "../../../../api/http";
import type { WorkspaceSummary } from "../../../../api/types";
import { GateStudioPanel } from "../GateStudioPanel";
import { agent, profile, stage, workflow, workspace } from "./gateStudioFixtures";

jest.mock("../../../../api/client", () => {
  const originalClient = jest.requireActual("../../../../api/client");
  return {
    ...originalClient,
    api: {
      ...originalClient.api,
      orchestrationProfile: jest.fn(),
      studioWorkflows: jest.fn(),
      studioAgents: jest.fn(),
      updateWorkspaceGates: jest.fn(),
      testWorkspaceGates: jest.fn(),
    },
  };
});

const { api } = require("../../../../api/client");

let lastPath = "";
function PathProbe() {
  lastPath = useLocation().pathname;
  return null;
}

function renderAt(
  path: string,
  {
    workspaces = [workspace()],
    active,
    error = null,
    retry = () => {},
  }: { workspaces?: WorkspaceSummary[]; active?: string; error?: unknown; retry?: () => void } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/studio/gates/*"
            element={
              <>
                <PathProbe />
                <GateStudioPanel
                  workspaces={workspaces}
                  workspacesLoading={false}
                  workspacesError={error}
                  onRetryWorkspaces={retry}
                  activeWorkspaceSlug={active}
                />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const gateStage = stage({
  key: "gate",
  name: "Gate",
  stage_type: "gate",
  exit_actions_enabled: true,
  exit_actions: [
    { key: "sign-off", label: "Sign off", requirement: { kind: "operator_judgment", decision_prompt: "Ship it?" } },
  ],
});

beforeEach(() => {
  jest.clearAllMocks();
  api.orchestrationProfile.mockResolvedValue(profile());
  api.studioWorkflows.mockResolvedValue([
    workflow({ slug: "other-flow", name: "Other Flow", published_template_slug: "other-flow" }),
    workflow({ stages: [stage(), gateStage] }),
  ]);
  api.studioAgents.mockResolvedValue([agent()]);
});

describe("GateStudioPanel", () => {
  it("opens a bare /studio/gates on the app-wide active workspace", async () => {
    renderAt("/studio/gates", { workspaces: [workspace(), workspace({ slug: "loregarden", name: "Loregarden" })], active: "loregarden" });

    await waitFor(() => expect(lastPath).toBe("/studio/gates/loregarden"));
    await waitFor(() => expect(api.orchestrationProfile).toHaveBeenCalledWith("loregarden"));
  });

  it("lists every workflow with the workspace's own pinned first and badged", async () => {
    renderAt("/studio/gates/blobert");

    const nav = await screen.findByRole("navigation", { name: "Workflows for blobert" });
    const links = await within(nav).findAllByRole("link");
    expect(links[0]).toHaveTextContent("Blobert TDD");
    expect(links[0]).toHaveTextContent("Runs here");
    expect(links[1]).toHaveTextContent("Other Flow");
    expect(links[1]).not.toHaveTextContent("Runs here");
  });

  it("shows each kind in its own section, with an empty state where a workflow has none", async () => {
    renderAt("/studio/gates/blobert/blobert-tdd");

    expect(await screen.findByRole("heading", { name: "Gate stages" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Gate" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign off" })).toBeInTheDocument();
    expect(await screen.findByText(/No agent this workflow runs has handoff checks/)).toBeInTheDocument();
  });

  it("renders the same detail from a deep link as from clicking through", async () => {
    const first = renderAt("/studio/gates/blobert/blobert-tdd/exit-action:gate:sign-off");
    const deep = (await screen.findByRole("heading", { level: 2, name: "Sign off" })).closest("article")?.textContent;
    first.unmount();

    renderAt("/studio/gates/blobert/blobert-tdd");
    fireEvent.click(await screen.findByRole("link", { name: "Sign off" }));
    const clicked = (await screen.findByRole("heading", { level: 2, name: "Sign off" })).closest("article")?.textContent;

    expect(clicked).toBe(deep);
    expect(clicked).toContain("Operator judgement — Ship it?");
    expect(lastPath).toBe("/studio/gates/blobert/blobert-tdd/exit-action:gate:sign-off");
  });

  it("links a read-only control to the studio that owns it", async () => {
    renderAt("/studio/gates/blobert/blobert-tdd/gate-stage:gate");
    expect(await screen.findByRole("link", { name: "Edit workflow blobert-tdd in Workflow Studio" })).toHaveAttribute(
      "href",
      "/studio/workflows/blobert-tdd",
    );
  });

  it("opens a transition command's detail with the editor beneath it", async () => {
    renderAt("/studio/gates/blobert/_/transition-command:0");
    expect(await screen.findByRole("heading", { level: 2, name: "ruff check {workspace_root}" })).toBeInTheDocument();
    expect(await screen.findByDisplayValue("ruff check {workspace_root}")).toBeInTheDocument();
  });

  it("asks before a link discards unsaved transition edits", async () => {
    renderAt("/studio/gates/blobert");
    fireEvent.change(await screen.findByDisplayValue("ruff check {workspace_root}"), { target: { value: "edited" } });

    fireEvent.click(screen.getByRole("link", { name: /Other Flow/ }));
    expect(screen.getByText(/unsaved gate changes for blobert/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(lastPath).toBe("/studio/gates/blobert");
    expect(screen.getByDisplayValue("edited")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: /Other Flow/ }));
    fireEvent.click(screen.getByRole("button", { name: "Discard and leave" }));
    await waitFor(() => expect(lastPath).toBe("/studio/gates/blobert/other-flow"));
  });

  it("reloads the editor when switching workspaces", async () => {
    api.orchestrationProfile.mockImplementation((slug: string) =>
      Promise.resolve(slug === "blobert" ? profile() : profile({ slug: "loregarden", gates_commands: ["pytest"] })),
    );
    renderAt("/studio/gates/blobert", { workspaces: [workspace(), workspace({ slug: "loregarden", name: "Loregarden" })] });

    await screen.findByDisplayValue("ruff check {workspace_root}");
    fireEvent.click(screen.getByRole("link", { name: "Loregarden" }));
    expect(await screen.findByDisplayValue("pytest")).toBeInTheDocument();
  });

  it.each([
    ["/studio/gates/nope", /No workspace named nope/],
    ["/studio/gates/blobert/missing-flow", /No workflow named missing-flow for blobert/],
    ["/studio/gates/blobert/blobert-tdd/gate-stage:gone", /Nothing named gate-stage:gone in blobert-tdd/],
  ])("names what was not found at %s", async (path, message) => {
    renderAt(path);
    expect(await screen.findByText(message)).toBeInTheDocument();
  });

  it("says when the gate settings are not readable, naming the workspace", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(403, "forbidden"));
    renderAt("/studio/gates/blobert");
    expect(await screen.findByText(/don't have permission to read the gate settings for blobert/)).toBeInTheDocument();
  });

  // lg-milestone-that-1215 AC13. This panel is the surface that *writes* these
  // profile files, so a half-written or hand-edited one is its own output coming
  // back. Until now only a 403 was handled: every other failure fell through to
  // `collectWorkspaceControls(workspace, null)`, which renders a plausible,
  // complete-looking gate list for a file nobody could read. The question the
  // operator arrives with is "why are my gates not what I set?", and an empty
  // list is the one answer that stops them finding out.
  const UNREADABLE_PROFILE_PATH = "/repo/loregarden/agent_context/orchestration/default.yaml";
  const unreadableProfileError = () =>
    new ApiError(
      400,
      `${UNREADABLE_PROFILE_PATH} could not be read as an orchestration profile: while parsing a flow sequence, expected ',' or ']'`,
    );

  it("says the gate file is broken, and names the file, when the profile will not load", async () => {
    api.orchestrationProfile.mockRejectedValue(unreadableProfileError());
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Gate settings for blobert could not be read");
    expect(alert.textContent).toContain(UNREADABLE_PROFILE_PATH);
    expect(alert).toHaveTextContent("Fix that file and reload.");
  });

  it("renders no gate list at all when the profile will not load", async () => {
    api.orchestrationProfile.mockRejectedValue(unreadableProfileError());
    const { container } = renderAt("/studio/gates/blobert/blobert-tdd");

    await screen.findByRole("alert");
    expect(container.querySelector(".gate-studio-controls")).toBeNull();
    // The error state and the legitimate empty state must not read alike: the
    // empty copy here would say this workflow has no controls of a kind, which
    // is a different and perfectly fine answer.
    expect(screen.queryByText(/No agent this workflow runs has handoff checks/)).not.toBeInTheDocument();
  });

  it("keeps the 403 copy, and keeps it ahead of the general failure branch", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(403, UNREADABLE_PROFILE_PATH));
    renderAt("/studio/gates/blobert");

    expect(await screen.findByText(/don't have permission to read the gate settings for blobert/)).toBeInTheDocument();
    // A general `isError` branch placed *before* the 403 one would swallow it,
    // and the operator would be told to go and fix a file they are not allowed
    // to read.
    expect(screen.queryByText(/could not be read:/)).not.toBeInTheDocument();
  });

  it("says what failed when the workflows cannot load, and retries", async () => {
    api.studioWorkflows.mockRejectedValueOnce(new Error("boom"));
    renderAt("/studio/gates/blobert");
    expect(await screen.findByText(/Couldn't load the workflows for blobert.*boom/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("navigation", { name: "Workflows for blobert" })).toBeInTheDocument();
  });

  it("says the workspace list failed rather than that there are none, and retries", () => {
    const retry = jest.fn();
    renderAt("/studio/gates", { workspaces: [], error: new Error("502 Bad Gateway"), retry });

    expect(screen.getByText(/Couldn't load the workspaces.*502/)).toBeInTheDocument();
    expect(screen.queryByText(/No workspaces yet/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalled();
  });

  it("says what to do when there are no workspaces", () => {
    renderAt("/studio/gates", { workspaces: [] });
    expect(screen.getAllByText(/No workspaces yet/).length).toBeGreaterThan(0);
  });
});
