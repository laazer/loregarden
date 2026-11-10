/**
 * The view delete's confirmation, and specifically its body's keyboard path.
 *
 * lg-milestone-that-2215: `.modal-body` is bounded with `overflow: auto`, and
 * every control this dialog has — the close cross, `Cancel`, `Delete view` —
 * sits OUTSIDE the body. So nothing inside it could take the focus the arrow
 * keys, Page Up/Down and Home/End scroll from, and the text was unreachable
 * without a mouse once it overflowed.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DeleteViewConfirmModal } from "../DeleteViewConfirmModal";
import type { ViewSummary } from "../../lib/viewsApi";

const VIEW = { id: "v1", title: "Release board", icon: "", kind: "flex_grid" } as unknown as ViewSummary;
const REGION = "What deleting this view does";

function renderModal(overrides: { view?: ViewSummary | null; isDeleting?: boolean } = {}) {
  const onClose = jest.fn();
  const onConfirm = jest.fn();
  render(
    <DeleteViewConfirmModal
      view={overrides.view === undefined ? VIEW : overrides.view}
      isDeleting={overrides.isDeleting ?? false}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  return { onClose, onConfirm };
}

describe("the delete-view dialog body's keyboard path", () => {
  it("is a tab stop that steps on to the footer's Cancel — AC3", async () => {
    renderModal();

    const body = screen.getByRole("group", { name: REGION });
    expect(body).toHaveAttribute("tabindex", "0");
    expect(body).toHaveClass("scroll-focus-ring");

    // `.modal-footer` FOLLOWS the body, so Tab forward is the step this
    // surface offers; there is no control between the two.
    body.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("renders no labelled body while the dialog is closed — AC8", () => {
    renderModal({ view: null });

    // A closed shell keeps its last content for the exit animation. A named
    // region left in the tab order over it is a stop that announces a name
    // with nothing behind it.
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
  });

  it("lets Escape from inside the body still close the dialog — AC9", () => {
    const { onClose } = renderModal();

    const body = screen.getByRole("group", { name: REGION });
    body.focus();
    fireEvent.keyDown(body, { key: "Escape" });
    // The region binds no key of its own, so `useDialogDismiss` still sees it.
    expect(onClose).toHaveBeenCalled();
  });
});
