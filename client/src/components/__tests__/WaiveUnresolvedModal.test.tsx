import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WaiveUnresolvedModal } from "../WaiveUnresolvedModal";

const NAMES = ["handoff:test_suite_complete incomplete", "evidence:real_surface missing"];

describe("WaiveUnresolvedModal", () => {
  it("renders nothing when closed", () => {
    render(
      <WaiveUnresolvedModal
        open={false}
        names={NAMES}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("names the dialog Waive unresolved items? and lists names as code, not checkboxes", () => {
    render(
      <WaiveUnresolvedModal open names={NAMES} onClose={() => {}} onConfirm={() => {}} />,
    );

    expect(screen.getByRole("dialog", { name: /waive unresolved items\?/i })).toBeInTheDocument();
    for (const name of NAMES) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(document.querySelector("ul code")).not.toBeNull();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText(/loading/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("focuses the reason textarea on open", () => {
    render(
      <WaiveUnresolvedModal open names={NAMES} onClose={() => {}} onConfirm={() => {}} />,
    );
    expect(screen.getByRole("textbox")).toHaveFocus();
  });

  it("disables Waive and continue until a trimmed reason is entered, then submits it", () => {
    const onConfirm = jest.fn();
    render(
      <WaiveUnresolvedModal open names={NAMES} onClose={() => {}} onConfirm={onConfirm} />,
    );

    const submit = screen.getByRole("button", { name: "Waive and continue" });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
    expect(submit).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "  accepted the remaining gap  " },
    });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    expect(onConfirm).toHaveBeenCalledWith({ response: "accepted the remaining gap" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("cancel closes without submitting", () => {
    const onClose = jest.fn();
    const onConfirm = jest.fn();
    render(
      <WaiveUnresolvedModal
        open
        names={NAMES}
        onClose={onClose}
        onConfirm={onConfirm}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("does not fire onConfirm twice when Waive and continue is clicked twice", () => {
    const onConfirm = jest.fn();
    render(
      <WaiveUnresolvedModal open names={NAMES} onClose={() => {}} onConfirm={onConfirm} />,
    );

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "accepted the remaining gap" } });
    const submit = screen.getByRole("button", { name: "Waive and continue" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("keeps the typed reason after confirm while the modal stays open", () => {
    render(
      <WaiveUnresolvedModal open names={NAMES} onClose={() => {}} onConfirm={() => {}} />,
    );

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "accepted the remaining gap" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Waive and continue" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("accepted the remaining gap");
  });

  it("while submitting, disables submit and cancel, labels Waiving…, and ignores Escape", async () => {
    const user = userEvent.setup();
    const onClose = jest.fn();
    const onConfirm = jest.fn();
    render(
      <WaiveUnresolvedModal
        open
        names={NAMES}
        isSubmitting
        onClose={onClose}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByRole("button", { name: "Waiving…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("textbox")).toBeDisabled();

    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: /waive unresolved items\?/i })).toBeInTheDocument();
  });
});
