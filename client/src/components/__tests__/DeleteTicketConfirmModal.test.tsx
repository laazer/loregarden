/**
 * The work-item delete's confirmation, and specifically its body's keyboard
 * path.
 *
 * lg-milestone-that-2215: `.modal-body` is bounded with `overflow: auto`, and
 * `Cancel` and the destructive confirm both sit in `.modal-footer` OUTSIDE it,
 * so nothing in the body could take the focus the scroll keys act from.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DeleteTicketConfirmModal } from "../DeleteTicketConfirmModal";
import type { TicketDetail } from "../../api/client";

const TICKET = {
  id: "t1",
  external_id: "lg-milestone-that-1",
  title: "Wire the CLI",
  work_item_type: "task",
} as unknown as TicketDetail;
const REGION = "What deleting this ticket does";

function renderModal(overrides: { open?: boolean; ticket?: TicketDetail | null } = {}) {
  const onClose = jest.fn();
  const onConfirm = jest.fn();
  render(
    <DeleteTicketConfirmModal
      open={overrides.open ?? true}
      ticket={overrides.ticket === undefined ? TICKET : overrides.ticket}
      isDeleting={false}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  return { onClose, onConfirm };
}

describe("the delete-ticket dialog body's keyboard path", () => {
  it("is a tab stop that steps on to the footer's Cancel — AC3", async () => {
    renderModal();

    const body = screen.getByRole("group", { name: REGION });
    expect(body).toHaveAttribute("tabindex", "0");
    expect(body).toHaveClass("scroll-focus-ring");

    body.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("renders no labelled body while the dialog is closed — AC8", () => {
    renderModal({ open: false, ticket: null });

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
