/**
 * The trap is exercised through real Tab presses rather than through the
 * handler, because the question is where focus *ends up*: `user-event`'s `tab()`
 * computes the document's own tab order and honours `preventDefault`, so a test
 * that passes here would have passed in a browser for the same reason.
 *
 * The page behind the dialog is part of every fixture. A trap that is never
 * asked to keep focus away from anything is not being tested.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import { tabbableWithin, useDialogFocusTrap } from "../useDialogFocusTrap";

function Dialog({ children }: { children?: ReactNode }) {
  const ref = useDialogFocusTrap<HTMLDivElement>();
  return (
    <div ref={ref} role="dialog" aria-label="Dialog">
      {children ?? (
        <>
          <button type="button">First</button>
          <button type="button">Second</button>
        </>
      )}
    </div>
  );
}

/** The dialog, plus the page it is drawn over and must not leak focus to. */
function Page({ open, children }: { open: boolean; children?: ReactNode }) {
  return (
    <>
      <button type="button">Behind before</button>
      {open ? <Dialog>{children}</Dialog> : null}
      <button type="button">Behind after</button>
    </>
  );
}

describe("useDialogFocusTrap", () => {
  it("takes focus when the dialog opens", () => {
    render(<Page open />);
    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();
  });

  it("leaves focus alone when the dialog already placed it", () => {
    render(
      <Page open>
        <button type="button">First</button>
        {/* eslint-disable-next-line jsx-a11y/no-autofocus */}
        <input autoFocus aria-label="Search" />
      </Page>,
    );
    expect(screen.getByLabelText("Search")).toHaveFocus();
  });

  it("wraps forward off the last control instead of reaching the page behind", async () => {
    const user = userEvent.setup();
    render(<Page open />);

    await user.tab();
    expect(screen.getByRole("button", { name: "Second" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();
  });

  it("wraps backward off the first control", async () => {
    const user = userEvent.setup();
    render(<Page open />);

    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Second" })).toHaveFocus();
  });

  it("pulls focus back in when it is somewhere behind the overlay", async () => {
    const user = userEvent.setup();
    render(<Page open />);

    // The control *after* the dialog, deliberately: from the one before it, the
    // document's own tab order already leads to the dialog's first button, and
    // a test starting there passes whether or not anything is trapping.
    screen.getByRole("button", { name: "Behind after" }).focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();

    screen.getByRole("button", { name: "Behind after" }).focus();
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Second" })).toHaveFocus();
  });

  it("leaves Tab to a control that claims it", async () => {
    const user = userEvent.setup();

    // Monaco does this: it owns Tab for indentation, and the `edit` chat
    // primitive puts it inside `PrimitiveSlot`'s modal overlay. A trap that
    // cycled focus first would break indenting mid-keystroke.
    function TabEater() {
      return (
        <textarea
          aria-label="Editor"
          onKeyDown={(event) => {
            if (event.key === "Tab") event.preventDefault();
          }}
        />
      );
    }

    render(
      <Page open>
        <button type="button">First</button>
        <TabEater />
      </Page>,
    );

    const editor = screen.getByLabelText("Editor");
    editor.focus();
    await user.tab();

    // Last tabbable in the dialog, so an unconditional trap would have wrapped
    // to "First" here.
    expect(editor).toHaveFocus();
  });

  it("holds focus on the dialog itself when it contains nothing tabbable", async () => {
    const user = userEvent.setup();
    render(
      <Page open>
        <p>Nothing to focus.</p>
      </Page>,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveFocus();

    await user.tab();
    expect(dialog).toHaveFocus();
  });

  it("arms when a dialog that returns null first renders its panel", () => {
    // The shape half these modals use: the hook is called unconditionally, and
    // the element it traps does not exist until there is something to show. A
    // trap keyed on mount rather than on the node sees `null` here and never
    // looks again.
    function LateDialog({ view }: { view: string | null }) {
      const ref = useDialogFocusTrap<HTMLDivElement>();
      if (view === null) return null;
      return (
        <div ref={ref} role="dialog" aria-label={view}>
          <button type="button">First</button>
        </div>
      );
    }

    const { rerender } = render(<LateDialog view={null} />);
    rerender(<LateDialog view="Delete view?" />);

    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();
  });

  it("restores focus to the opener when the dialog unmounts", () => {
    function Host({ open }: { open: boolean }) {
      return (
        <>
          <button type="button">Opener</button>
          {open ? <Dialog /> : null}
        </>
      );
    }

    const { rerender } = render(<Host open={false} />);
    const opener = screen.getByRole("button", { name: "Opener" });
    opener.focus();

    rerender(<Host open />);
    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();

    rerender(<Host open={false} />);
    expect(opener).toHaveFocus();
  });

  it("survives an opener that the same interaction removed", () => {
    function Host({ step }: { step: number }) {
      return (
        <>
          {step === 0 ? <button type="button">Opener</button> : null}
          {step === 1 ? <Dialog /> : null}
        </>
      );
    }

    const { rerender } = render(<Host step={0} />);
    screen.getByRole("button", { name: "Opener" }).focus();

    rerender(<Host step={1} />);
    expect(() => rerender(<Host step={2} />)).not.toThrow();
  });

  it("steers from the innermost dialog only", async () => {
    const user = userEvent.setup();

    function Nested() {
      return (
        <>
          <Dialog>
            <button type="button">Outer only</button>
          </Dialog>
          <Dialog>
            <button type="button">Inner first</button>
            <button type="button">Inner second</button>
          </Dialog>
        </>
      );
    }

    render(<Nested />);
    expect(screen.getByRole("button", { name: "Inner first" })).toHaveFocus();

    await user.tab();
    expect(screen.getByRole("button", { name: "Inner second" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Inner first" })).toHaveFocus();
  });
});

describe("tabbableWithin", () => {
  it("skips what the platform will not tab to", () => {
    const root = document.createElement("div");
    root.innerHTML = `
      <button id="ok">ok</button>
      <button disabled>disabled</button>
      <input aria-hidden="true" />
      <div hidden><button id="buried">buried</button></div>
      <div tabindex="-1">programmatic only</div>
      <div tabindex="0" id="custom">custom stop</div>
    `;
    document.body.appendChild(root);

    expect(tabbableWithin(root).map((element) => element.id)).toEqual(["ok", "custom"]);

    root.remove();
  });
});
