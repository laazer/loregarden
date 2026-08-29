/**
 * The trap is exercised through real Tab presses rather than through the
 * handler, because the question is where focus *ends up*: `user-event`'s `tab()`
 * computes the document's own tab order and honours `preventDefault`, so a test
 * that passes here would have passed in a browser for the same reason.
 *
 * The page behind the dialog is part of every fixture. A trap that is never
 * asked to keep focus away from anything is not being tested.
 */

import { render, screen, waitFor } from "@testing-library/react";
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

  it.each([
    ["a plain dialog", null],
    ["a dialog that autofocuses a field", "Search"],
  ])("restores focus to the opener when %s unmounts", (_label, autoFocused) => {
    // The autofocus case is not a variation, it is the one that broke: React
    // applies `autoFocus` while committing the dialog's children, before any
    // effect here runs, so an opener remembered from inside the effect is the
    // dialog's own field — and restoring to it after unmount lands on `<body>`.
    function Host({ open }: { open: boolean }) {
      return (
        <>
          <button type="button">Opener</button>
          {open ? (
            <Dialog>
              <button type="button">First</button>
              {/* eslint-disable-next-line jsx-a11y/no-autofocus */}
              {autoFocused === null ? null : <input autoFocus aria-label={autoFocused} />}
            </Dialog>
          ) : null}
        </>
      );
    }

    const { rerender } = render(<Host open={false} />);
    const opener = screen.getByRole("button", { name: "Opener" });
    opener.focus();

    rerender(<Host open />);
    expect(
      autoFocused === null ? screen.getByRole("button", { name: "First" }) : screen.getByLabelText(autoFocused),
    ).toHaveFocus();

    rerender(<Host open={false} />);
    expect(opener).toHaveFocus();
  });

  it("restores focus when a dialog that stays mounted drops its panel", async () => {
    // The `if (!view) return null` shape closing, rather than being unmounted:
    // the ref detaches while the component lives on, so the trap tears down
    // through a re-render rather than an unmount.
    function LateDialog({ view }: { view: string | null }) {
      const ref = useDialogFocusTrap<HTMLDivElement>();
      if (view === null) return null;
      return (
        <div ref={ref} role="dialog" aria-label={view}>
          <button type="button">Confirm</button>
        </div>
      );
    }

    function Host({ view }: { view: string | null }) {
      return (
        <>
          <button type="button">Opener</button>
          <LateDialog view={view} />
        </>
      );
    }

    const { rerender } = render(<Host view={null} />);
    const opener = screen.getByRole("button", { name: "Opener" });
    opener.focus();

    rerender(<Host view="Delete view?" />);
    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();

    rerender(<Host view={null} />);
    await waitFor(() => expect(opener).toHaveFocus());
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
  function tabbableIds(html: string): string[] {
    const root = document.createElement("div");
    root.innerHTML = html;
    document.body.appendChild(root);
    try {
      return tabbableWithin(root).map((element) => element.id);
    } finally {
      root.remove();
    }
  }

  it("skips what the platform will not tab to", () => {
    expect(
      tabbableIds(`
        <button id="ok">ok</button>
        <button disabled>disabled</button>
        <button aria-disabled="true">off</button>
        <fieldset disabled><button id="in-fieldset">grouped off</button></fieldset>
        <input aria-hidden="true" />
        <div hidden><button id="buried">buried</button></div>
        <div inert><button id="inert">inert</button></div>
        <div contenteditable="false" id="not-editable"></div>
        <div tabindex="-1">programmatic only</div>
        <div tabindex="0" id="custom">custom stop</div>
      `),
    ).toEqual(["ok", "custom"]);
  });

  it("counts a radio group as the one stop the browser makes", () => {
    // `UpdateStateModal` has an eight-option `role="radiogroup"`. Counting each
    // radio as its own stop would put seven phantom ones between the real
    // controls, and the wrap — computed from the edges of this list — would
    // land somewhere the Tab key never goes.
    expect(
      tabbableIds(`
        <input type="radio" name="state" id="backlog" />
        <input type="radio" name="state" id="doing" checked />
        <input type="radio" name="state" id="done" />
        <input type="radio" id="unnamed" />
      `),
    ).toEqual(["doing", "unnamed"]);

    expect(
      tabbableIds(`
        <input type="radio" name="state" id="first" />
        <input type="radio" name="state" id="second" />
      `),
    ).toEqual(["first"]);
  });
});
