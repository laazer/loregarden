/**
 * The toolchain-gates panel's failing-check output, and its keyboard path.
 *
 * lg-milestone-that-2215: `.gate-preset-output` is a bounded `overflow: auto`
 * <pre> holding the last eight lines a refused check printed. It is the whole
 * reason the panel refuses to save — and nothing in it took focus, so those
 * lines could not be scrolled with the arrow keys, which is where the reason
 * actually is when there are more than a few.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";

import { WorkspaceGatePresetsPanel } from "../WorkspaceGatePresetsPanel";
import { api } from "../../../api/client";
import type { GatePresets } from "../../../api/gatePresetTypes";
import type { GateTestCommandResult, OrchestrationProfileView } from "../../../api/gateTypes";

jest.mock("../../../api/client", () => ({
  api: {
    gatePresetsForWorkspace: jest.fn(),
    orchestrationProfile: jest.fn(),
    testWorkspaceGates: jest.fn(),
    updateWorkspaceGates: jest.fn(),
  },
}));
const mockPresets = api.gatePresetsForWorkspace as jest.MockedFunction<
  typeof api.gatePresetsForWorkspace
>;
const mockProfile = api.orchestrationProfile as jest.MockedFunction<typeof api.orchestrationProfile>;
const mockTest = api.testWorkspaceGates as jest.MockedFunction<typeof api.testWorkspaceGates>;

const COMMAND = "ruff check .";
const TEMPLATE = "ruff";

const PRESETS: GatePresets = {
  repo_root: "/Users/jacobbrandt/workspace/blobert",
  toolchains: [
    {
      key: "python",
      label: "Python",
      directory: ".",
      detected: true,
      note: "",
      commands: [
        { command: COMMAND, label: "Ruff", kind: "lint", default_on: true, running_as: null },
      ],
    },
  ],
};

const PROFILE = {
  slug: "blobert",
  name: "Blobert",
  gates_enabled: true,
  gates_configured: true,
  gates_commands: [],
  gates_autofix_commands: [],
} as unknown as OrchestrationProfileView;

function failing(): GateTestCommandResult {
  return {
    template: TEMPLATE,
    command: COMMAND,
    outcome: "failed",
    message: "Exited 1",
    stdout: "",
    stderr: [
      "a.py:1:1: F401 imported but unused",
      "b.py:9:5: E501 line too long",
      "c.py:3:1: F841 local variable assigned but never used",
    ].join("\n"),
    duration_ms: 900,
  };
}

const REGION = `Output from ${TEMPLATE}`;

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
  return render(<WorkspaceGatePresetsPanel slug="blobert" name="Blobert" />, { wrapper });
}

/** Tick the offered check, then ask to save, which runs it and refuses. */
async function saveAndFail() {
  renderPanel();
  await userEvent.click(await screen.findByRole("checkbox", { name: /Ruff/ }));
  await userEvent.click(screen.getByRole("button", { name: "Check and save" }));
  await waitFor(() => expect(mockTest).toHaveBeenCalled());
}

beforeEach(() => {
  jest.clearAllMocks();
  mockPresets.mockResolvedValue(PRESETS);
  mockProfile.mockResolvedValue(PROFILE);
});

describe("the refused check's output pane", () => {
  it("is a tab stop that steps on to Save anyway — AC3", async () => {
    mockTest.mockResolvedValue({ results: [failing()] } as never);
    await saveAndFail();

    const pane = await screen.findByRole("group", { name: REGION });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");

    // The actions FOLLOW the refusal block, and `Save anyway` only exists
    // because a check failed — it is the decision this output leads to.
    pane.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Save anyway" })).toHaveFocus();
  });

  it("renders no labelled pane for a check that printed nothing — AC8", async () => {
    // A check can refuse with only a message and no output at all, and the
    // refusal line above still says which check and why. A named box over
    // nothing is the dead tab stop AC8 forbids.
    mockTest.mockResolvedValue({
      results: [{ ...failing(), stderr: "", stdout: "" }],
    } as never);
    await saveAndFail();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
  });

  it("binds no key of its own, so Escape passes through — AC9", async () => {
    mockTest.mockResolvedValue({ results: [failing()] } as never);
    await saveAndFail();

    const pane = await screen.findByRole("group", { name: REGION });
    pane.focus();
    fireEvent.keyDown(pane, { key: "Escape" });
    // This panel is not a dialog, so nothing should close; what matters is
    // that the region does not consume the key on its way to whatever would.
    expect(pane).toHaveFocus();
  });
});
