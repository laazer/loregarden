/** The delete confirms before it destroys.
 *
 * AC10 and AC11 of lg-milestone-that-1239. `delete_agent` has never once
 * succeeded — every db-backed agent has at least one version row, so the FK
 * ordering bug fires 100% of the time — which means AC1 arms a one-click
 * irreversible destroy for the first time. Agents carry 10.9 version rows on
 * average and up to 17, all of them going with the row. Because AC1's reference
 * scan refuses every referenced agent, this confirm is only ever reached for an
 * agent nothing uses: rare enough that it will still be read.
 */

import { fireEvent, render, screen } from "@testing-library/react";

import { AgentDeleteConfirm } from "../AgentDeleteConfirm";

interface Overrides {
  open?: boolean;
  slug?: string;
  versionCount?: number;
  pending?: boolean;
  onConfirm?: () => void;
  onCancel?: () => void;
}

function renderConfirm(overrides: Overrides = {}) {
  const props = {
    open: true,
    slug: "godot-ddd-migrator",
    versionCount: 11,
    pending: false,
    onConfirm: jest.fn(),
    onCancel: jest.fn(),
    ...overrides,
  };
  return { ...render(<AgentDeleteConfirm {...props} />), props };
}

function deleteAction() {
  return screen.getByRole("button", { name: /^deleting…$|^delete agent$/i });
}

describe("AgentDeleteConfirm", () => {
  it("renders nothing until it is opened", () => {
    renderConfirm({ open: false });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("names the agent and how many stored versions go with it", () => {
    renderConfirm();

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("godot-ddd-migrator");
    expect(dialog).toHaveTextContent("11");
  });

  it("is a labelled modal dialog", () => {
    renderConfirm();

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby");
    expect(screen.getByRole("dialog")).toContainElement(
      document.getElementById(dialog.getAttribute("aria-labelledby")!),
    );
  });

  it("destroys only from its own Delete agent action", () => {
    const { props } = renderConfirm();

    expect(props.onConfirm).not.toHaveBeenCalled();
    fireEvent.click(deleteAction());

    expect(props.onConfirm).toHaveBeenCalledTimes(1);
  });

  it("cancels without destroying", () => {
    const { props } = renderConfirm();

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(props.onCancel).toHaveBeenCalledTimes(1);
    expect(props.onConfirm).not.toHaveBeenCalled();
  });

  it("dismisses on Escape while idle", () => {
    const { props } = renderConfirm();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it("cannot be dismissed by Escape while the delete is in flight", () => {
    // ModalShell's `onDismiss` is undefined while pending, so neither Escape
    // nor the backdrop closes a dialog whose request is already away.
    const { props } = renderConfirm({ pending: true });

    fireEvent.keyDown(document, { key: "Escape" });

    expect(props.onCancel).not.toHaveBeenCalled();
  });

  it("disables its Delete action while pending and says what is happening", () => {
    renderConfirm({ pending: true });

    const action = screen.getByRole("button", { name: /deleting…/i });
    expect(action).toBeDisabled();
  });

  it("a second click cannot fire the destroy twice", () => {
    const { props } = renderConfirm({ pending: true });

    fireEvent.click(screen.getByRole("button", { name: /deleting…/i }));

    expect(props.onConfirm).not.toHaveBeenCalled();
  });

  it("uses the app's button primitive for its controls", () => {
    // AC13: a hand-rolled overlay and a raw <button> both fail the gates.
    renderConfirm();

    for (const control of screen.getAllByRole("button")) {
      expect(control.className).toMatch(/btn-|ui-button/);
    }
  });

  it("puts focus inside the dialog when it opens", () => {
    renderConfirm();

    expect(screen.getByRole("dialog")).toContainElement(
      document.activeElement as HTMLElement | null,
    );
  });
});
