/**
 * Escape is pressed for real rather than dispatched at the handler, because the
 * question is who receives it: the hook competes with other listeners on
 * `document`, and only a real press through the document's own dispatch
 * reproduces that contest.
 *
 * Every fixture that tests the stack mounts two dialogs. A dismisser that is
 * never asked which press is its own is not being tested.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { useDialogDismiss } from "../useDialogDismiss";

function Dialog({ label, onClose }: { label: string; onClose: (() => void) | null }) {
  useDialogDismiss(onClose);
  return <div role="dialog" aria-label={label} />;
}

describe("useDialogDismiss", () => {
  it("closes the dialog on Escape", async () => {
    const onClose = jest.fn();
    render(<Dialog label="Settings" onClose={onClose} />);

    await userEvent.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("ignores every other key", async () => {
    const onClose = jest.fn();
    render(<Dialog label="Settings" onClose={onClose} />);

    await userEvent.keyboard("{Enter}{Tab}x");

    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes only the innermost dialog when two are stacked", async () => {
    const outer = jest.fn();
    const inner = jest.fn();
    render(
      <>
        <Dialog label="Panel" onClose={outer} />
        <Dialog label="Picker" onClose={inner} />
      </>,
    );

    await userEvent.keyboard("{Escape}");

    expect(inner).toHaveBeenCalledTimes(1);
    // The hand-rolled per-component effect this replaces closed both on one
    // press, because nothing decided whose press it was.
    expect(outer).not.toHaveBeenCalled();
  });

  it("hands the next press back to the dialog underneath", async () => {
    const outer = jest.fn();
    const inner = jest.fn();

    function Stack() {
      const [pickerOpen, setPickerOpen] = useState(true);
      return (
        <>
          <Dialog label="Panel" onClose={outer} />
          {pickerOpen && (
            <Dialog
              label="Picker"
              onClose={() => {
                inner();
                setPickerOpen(false);
              }}
            />
          )}
        </>
      );
    }

    render(<Stack />);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByLabelText("Picker")).not.toBeInTheDocument();

    await userEvent.keyboard("{Escape}");

    expect(inner).toHaveBeenCalledTimes(1);
    expect(outer).toHaveBeenCalledTimes(1);
  });

  it("registers nothing when there is no dismiss action", async () => {
    const outer = jest.fn();
    render(
      <>
        <Dialog label="Panel" onClose={outer} />
        <Dialog label="Inert" onClose={null} />
      </>,
    );

    await userEvent.keyboard("{Escape}");

    // A registered no-op would have swallowed this press and left the panel
    // underneath undismissable.
    expect(outer).toHaveBeenCalledTimes(1);
  });

  it("stops listening once the dialog unmounts", async () => {
    const onClose = jest.fn();
    const { unmount } = render(<Dialog label="Settings" onClose={onClose} />);

    unmount();
    await userEvent.keyboard("{Escape}");

    expect(onClose).not.toHaveBeenCalled();
  });

  it("survives a re-render that changes the callback identity", async () => {
    const calls: string[] = [];

    function Renaming() {
      const [name, setName] = useState("first");
      useDialogDismiss(() => calls.push(name));
      return (
        <button type="button" onClick={() => setName("second")}>
          Rename
        </button>
      );
    }

    render(<Renaming />);
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await userEvent.keyboard("{Escape}");

    // Reading through a ref: the listener stays put across re-renders, and still
    // calls the current handler rather than the one captured at mount.
    expect(calls).toEqual(["second"]);
  });

  it("leaves a press a control inside has already claimed", async () => {
    const onClose = jest.fn();

    function WithCombobox() {
      useDialogDismiss(onClose);
      return (
        <input
          aria-label="Filter"
          onKeyDown={(event) => {
            // What a combobox does when Escape closes its own popup.
            if (event.key === "Escape") event.preventDefault();
          }}
        />
      );
    }

    render(<WithCombobox />);
    await userEvent.click(screen.getByLabelText("Filter"));
    await userEvent.keyboard("{Escape}");

    expect(onClose).not.toHaveBeenCalled();
  });
});
