/**
 * The redraft confirmation, and specifically its body's keyboard path.
 *
 * lg-milestone-that-2215: `.modal-body` is bounded with `overflow: auto` and
 * this body is a list of everything a new draft would replace, which grows
 * with the number of target dates set. Both controls are in `.modal-footer`
 * OUTSIDE it, so the list could not be scrolled from the keyboard.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RedraftConfirmModal } from "../RedraftConfirmModal";
import type { ScheduleToReplace } from "../../../../lib/scheduleFormat";

const REPLACES: ScheduleToReplace = { targets: 4, pendingProposal: true };
const REGION = "What redrafting replaces";

function renderModal(replaces: ScheduleToReplace | null = REPLACES) {
  const onClose = jest.fn();
  const onConfirm = jest.fn();
  render(<RedraftConfirmModal replaces={replaces} onClose={onClose} onConfirm={onConfirm} />);
  return { onClose, onConfirm };
}

describe("the redraft dialog body's keyboard path", () => {
  it("is a tab stop that steps on to the footer's first control — AC3", async () => {
    renderModal();

    const body = screen.getByRole("group", { name: REGION });
    expect(body).toHaveAttribute("tabindex", "0");
    expect(body).toHaveClass("scroll-focus-ring");

    body.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Keep the current schedule" })).toHaveFocus();
  });

  it("renders no labelled body when there is no schedule to lose — AC8", () => {
    renderModal(null);

    // `replaces` is null exactly when nothing would be replaced, so there is
    // no list to read and the dialog is closed. A named region over that is
    // the dead tab stop AC8 forbids.
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
  });

  it("lets Escape from inside the body still close the dialog — AC9", () => {
    const { onClose } = renderModal();

    const body = screen.getByRole("group", { name: REGION });
    body.focus();
    fireEvent.keyDown(body, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
