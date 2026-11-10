/**
 * The composer's `/help` command strip, and its keyboard path.
 *
 * lg-milestone-that-2215: `.lg-composer-help-list` is a bounded
 * `overflow: auto` <ul> of every slash command. The only control near it is
 * the `Dismiss help` cross ABOVE it, so the list could not be scrolled from
 * the keyboard once it was longer than the strip.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ComposerNotes } from "../ComposerNotes";
import type { ComposerCommandsBinding } from "../../../hooks/useComposerCommands";
import type { ComposerCommand } from "../../../lib/composerCommands";

const COMMANDS: ComposerCommand[] = [
  { name: "help", aliases: [], summary: "List the commands", kind: "builtin" },
  { name: "queue", aliases: ["q"], summary: "Hold this message", kind: "builtin" },
  { name: "note", aliases: [], summary: "Keep a post-it", kind: "builtin" },
];

/** Only the fields `ComposerNotes` reads; the rest of the binding is the hook's. */
function binding(helpCommands: ComposerCommand[] | null): ComposerCommandsBinding {
  return {
    notes: [],
    draftNote: null,
    queued: [],
    helpCommands,
    closeHelp: jest.fn(),
    setDraftNote: jest.fn(),
    saveNote: jest.fn(),
    updateNote: jest.fn(),
    deleteNote: jest.fn(),
    sendNote: jest.fn(),
    sendNoteInNewChat: null,
    cancelQueued: jest.fn(),
  } as unknown as ComposerCommandsBinding;
}

const REGION = "Composer slash commands";

describe("the help strip's command list", () => {
  it("is a list, named, and a tab stop stepping back to Dismiss help — AC3", async () => {
    render(<ComposerNotes commands={binding(COMMANDS)} />);

    // `getByRole("list")`, not "group": a <ul> keeps its native `list` role so
    // it still announces "list, 3 items", which `group` would override.
    const list = screen.getByRole("list", { name: REGION });
    expect(list).toHaveAttribute("tabindex", "0");
    expect(list).toHaveClass("scroll-focus-ring");

    // `Dismiss help` PRECEDES the list, so Shift+Tab is the step that exists.
    list.focus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Dismiss help" })).toHaveFocus();
  });

  it("renders no labelled list while the help strip is closed — AC8", () => {
    render(<ComposerNotes commands={binding(null)} />);

    // `helpCommands` is null exactly when the strip is closed, so there is
    // never a named list with nothing in it.
    expect(screen.queryByRole("list", { name: REGION })).toBeNull();
  });

  it("binds no key of its own, so Escape is untouched — AC9", () => {
    const bound = binding(COMMANDS);
    render(<ComposerNotes commands={bound} />);

    const list = screen.getByRole("list", { name: REGION });
    list.focus();
    fireEvent.keyDown(list, { key: "Escape" });
    // The strip is not a dialog, so there is nothing for Escape to close
    // here — what matters is that the region does not swallow it on the way
    // to whatever does.
    expect(bound.closeHelp).not.toHaveBeenCalled();
    expect(list).toHaveFocus();
  });
});
