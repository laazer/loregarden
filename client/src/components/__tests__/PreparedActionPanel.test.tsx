/**
 * The prepared-action panel's captured output, and its keyboard path.
 *
 * lg-milestone-that-2215: `.prepared-action-output` is a bounded
 * `overflow: auto` <pre> holding whatever the script printed. The only control
 * near it is `Run and capture`, which PRECEDES it, so once the output was
 * longer than the box nothing could scroll it without a mouse.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PreparedActionPanel } from "../PreparedActionPanel";
import type { PreparedAction } from "../../api/chatTypes";
import { api } from "../../api/client";

jest.mock("../../api/client", () => ({
  api: { runHumanAction: jest.fn() },
}));
const mockRun = api.runHumanAction as jest.MockedFunction<typeof api.runHumanAction>;

function action(overrides: Partial<PreparedAction> = {}): PreparedAction {
  return {
    tier: "one_click",
    attempted: "Tried the push and the remote refused it.",
    prepared: "A script that re-points the upstream.",
    command: "scripts/repoint-upstream.sh",
    script_path: "scripts/repoint-upstream.sh",
    captures: ["the new upstream"],
    ...overrides,
  } as PreparedAction;
}

const REGION = "Captured output";

function renderPanel() {
  return render(
    <PreparedActionPanel approvalId="a1" action={action()} findings={[]} onDone={jest.fn()} />,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("the captured output's keyboard path", () => {
  it("is a tab stop that steps back to the Run button — AC3", async () => {
    mockRun.mockResolvedValue({ ok: true, output: "line one\nline two\nline three", exit_code: 0 } as never);
    renderPanel();

    await userEvent.click(screen.getByRole("button", { name: "Run and capture" }));
    const pane = await screen.findByRole("group", { name: REGION });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");

    // `Run and capture` PRECEDES the pane, so Shift+Tab is the step this
    // surface offers. Nothing follows the output.
    pane.focus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Run and capture" })).toHaveFocus();
  });

  it("renders no labelled pane before anything has been captured — AC8", async () => {
    renderPanel();

    // The pane is gated on `output`, so there is no state in which it draws a
    // named box over nothing. A run that failed shows its error in place.
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();

    mockRun.mockResolvedValue({ ok: false, output: "", error: "Exited 1", exit_code: 1 } as never);
    await userEvent.click(screen.getByRole("button", { name: "Run and capture" }));
    await waitFor(() => expect(screen.getByText("Exited 1")).toBeInTheDocument());
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
  });
});
