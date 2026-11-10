/**
 * The gate command list's test-output pane, and its keyboard path.
 *
 * lg-milestone-that-2215: `.gate-row-output` is a bounded `overflow: auto`
 * <pre> holding whatever the gate printed when it was tried. The `<summary>`
 * that opens it and the row's own controls sit OUTSIDE it, so a failing gate's
 * output could not be scrolled with the arrow keys once it overflowed — which
 * is exactly when a reader needs it.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { GateCommandList, type DraftCommand } from "../GateCommandList";
import type { GateTestCommandResult } from "../../../api/gateTypes";

const COMMAND = "pytest -q server/tests";

function result(overrides: Partial<GateTestCommandResult> = {}): GateTestCommandResult {
  return {
    template: "pytest",
    command: COMMAND,
    outcome: "failed",
    message: "Exited 1",
    stdout: "",
    stderr: ["E   AssertionError: boom", "E   and three more lines", "E   of output"].join("\n"),
    duration_ms: 1200,
    ...overrides,
  };
}

const REGION = "Test output";

function renderList(results?: Map<string, GateTestCommandResult>) {
  const commands: DraftCommand[] = [{ id: 1, value: COMMAND }];
  let id = 2;
  render(
    <GateCommandList
      noun="check"
      commands={commands}
      onChange={jest.fn()}
      newId={() => id++}
      placeholders={{ workspace_root: "/repo" }}
      results={results}
      emptyText="No checks yet."
      inputPlaceholder="A command to run"
    />,
  );
}

describe("the gate test output's keyboard path", () => {
  it("is a tab stop that steps back to the command field — AC3", async () => {
    renderList(new Map([[COMMAND, result()]]));

    // The <details> is already open, because the gate did not pass.
    const pane = screen.getByRole("group", { name: REGION });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");

    // The row's command textarea PRECEDES the pane, and is the thing a reader
    // goes back to edit after reading why the gate failed.
    pane.focus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("textbox", { name: "check 1 command" })).toHaveFocus();
  });

  it("renders no labelled pane for a gate that printed nothing — AC8", () => {
    // A gate that passed silently has no stderr, no stdout and no message to
    // show, so there is no <details> and no named box over nothing.
    renderList(new Map([[COMMAND, result({ outcome: "passed", message: "", stderr: "" })]]));

    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
    expect(screen.queryByText(REGION)).toBeNull();
  });

  it("binds no key of its own, so Escape passes through — AC9", () => {
    renderList(new Map([[COMMAND, result()]]));

    const pane = screen.getByRole("group", { name: REGION });
    pane.focus();
    fireEvent.keyDown(pane, { key: "Escape" });
    // This panel is not a dialog, so nothing should close — what matters is
    // that the region does not consume the key on its way to whatever would.
    expect(pane).toHaveFocus();
  });
});
