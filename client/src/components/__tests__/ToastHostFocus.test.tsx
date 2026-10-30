/**
 * S5 — keyboard and focus.
 *
 * The fifth of the five states, and the one a toast gets wrong in both
 * directions at once: it must not take focus (that interrupts typing, and the
 * operator did not ask for it), and it must give focus back when it goes away
 * under the keyboard operator who walked into it (otherwise they resume from
 * `<body>`, at the top of the document).
 *
 * S5.5's "nothing calls focus() at all" is asserted against a spy on
 * `HTMLElement.prototype.focus` rather than against `document.activeElement`.
 * The two are not the same claim: focusing a detached node leaves
 * `activeElement` as `<body>`, which is also what happens when nobody focuses
 * anything, so an activeElement assertion passes for the bug.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";

import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";
import { pushToast, useToastStore } from "../../state/toastStore";
import { ToastHost } from "../ToastHost";

beforeEach(() => {
  useToastStore.getState().clear();
});

/** A trigger outside the host, which is what focus has to come back to. */
function Page({ children }: { children?: ReactNode }) {
  return (
    <>
      <button type="button">Delete chat</button>
      {children}
      <ToastHost />
    </>
  );
}

const trigger = () => screen.getByRole("button", { name: "Delete chat" });

it("S5.1 — a toast appearing does not take focus from what the operator is typing in", () => {
  render(
    <>
      <label htmlFor="branch">Branch</label>
      <input id="branch" />
      <ToastHost />
    </>,
  );
  const input = screen.getByLabelText("Branch");
  act(() => input.focus());

  act(() => {
    pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
  });

  expect(input).toHaveFocus();
});

it("S5.2 — Escape does not dismiss a toast", async () => {
  const user = userEvent.setup();
  render(<ToastHost />);
  act(() => {
    pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
  });

  await user.keyboard("{Escape}");

  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(useToastStore.getState().toasts).toHaveLength(1);
});

it("S5.2 — and a dialog under it still closes on Escape", async () => {
  const user = userEvent.setup();

  function Dialog() {
    const [open, setOpen] = useState(true);
    if (!open) return <div>dialog closed</div>;
    return (
      <div
        role="dialog"
        aria-modal="true"
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
        }}
      >
        <button type="button">Confirm</button>
      </div>
    );
  }

  render(
    <Page>
      <Dialog />
    </Page>,
  );
  act(() => {
    pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
  });

  act(() => screen.getByRole("button", { name: "Confirm" }).focus());
  await user.keyboard("{Escape}");

  expect(screen.getByText("dialog closed")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toBeInTheDocument();
});

it("S5.3 — focus stays on the copy button after a copy", async () => {
  const writeText = jest.fn().mockResolvedValue(undefined);
  const user = userEvent.setup();
  // After setup(), which installs a clipboard stub of its own.
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

  render(<ToastHost />);
  act(() => {
    pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
  });

  const copy = screen.getByRole("button", { name: "Copy message: Delete session failed" });
  await user.click(copy);

  await waitFor(() => expect(writeText).toHaveBeenCalled());
  // The "Copied" state renames the same node; identity is what is asserted.
  expect(document.activeElement).toBe(copy);
});

it("S5.4 — closing the toast from inside it returns focus to the opener", async () => {
  const user = userEvent.setup();
  render(<Page />);
  act(() => {
    pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
  });

  act(() => trigger().focus());
  await user.tab(); // into the toast: the copy control is its first stop
  expect(screen.getByRole("button", { name: "Copy message: Delete session failed" })).toHaveFocus();
  await user.tab();
  const close = screen.getByRole("button", { name: "Dismiss: Delete session failed" });
  expect(close).toHaveFocus();

  await user.click(close);

  expect(trigger()).toHaveFocus();
  expect(document.body).not.toHaveFocus();
});

it("S5.5 — nothing is focused when the opener has left the document", async () => {
  const user = userEvent.setup();

  // The opener is whatever focus came FROM, so the element that has to vanish
  // is the one focused immediately before Tab entered the toast — not just any
  // button on the page. Removed by a re-render rather than by a second button,
  // because a second button would itself become the opener.
  function Vanishing({ triggerPresent }: { triggerPresent: boolean }) {
    return (
      <>
        {triggerPresent ? <button type="button">Delete chat</button> : null}
        <ToastHost />
      </>
    );
  }

  const view = render(<Vanishing triggerPresent />);
  act(() => {
    pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
  });

  act(() => trigger().focus());
  await user.tab();
  const copy = screen.getByRole("button", { name: "Copy message: Delete session failed" });
  expect(copy).toHaveFocus();

  // The interaction that dismisses the toast can also unmount the opener.
  act(() => {
    view.rerender(<Vanishing triggerPresent={false} />);
  });
  expect(screen.queryByRole("button", { name: "Delete chat" })).not.toBeInTheDocument();

  const close = screen.getByRole("button", { name: "Dismiss: Delete session failed" });
  const focusSpy = jest.spyOn(HTMLElement.prototype, "focus");
  try {
    // `fireEvent`, not `userEvent`: user-event's click calls `focus()` on the
    // element it clicks, which would make this spy assertion vacuous — it would
    // record user-event's own call and never see the toast's.
    fireEvent.click(close);
    expect(focusSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  } finally {
    focusSpy.mockRestore();
  }
});

it("S5.5 — nothing is focused when a toast dismisses itself on its timer", () => {
  jest.useFakeTimers();
  const focusSpy = jest.spyOn(HTMLElement.prototype, "focus");
  try {
    render(<Page />);
    act(() => {
      pushToast({ tone: "info", title: "Queued", duration: 2000 });
    });
    focusSpy.mockClear();

    act(() => {
      jest.advanceTimersByTime(2000);
    });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(focusSpy).not.toHaveBeenCalled();
  } finally {
    focusSpy.mockRestore();
    jest.useRealTimers();
  }
});

it("S5.5 — nothing is focused when clear() or an eviction takes a toast away", () => {
  render(<Page />);
  act(() => {
    pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
  });

  const focusSpy = jest.spyOn(HTMLElement.prototype, "focus");
  try {
    // An eviction first: a fifth distinct failure drops the oldest (MAX_TOASTS).
    act(() => {
      for (const n of [1, 2, 3, 4]) {
        pushToast({ tone: "error", title: `Action ${n} failed`, message: `detail ${n}` });
      }
    });
    expect(screen.queryByText("a path")).not.toBeInTheDocument();

    act(() => {
      useToastStore.getState().clear();
    });

    expect(focusSpy).not.toHaveBeenCalled();
  } finally {
    focusSpy.mockRestore();
  }
});

it("S5.5 — nothing is focused when focus was never inside the toast", () => {
  render(<Page />);
  act(() => {
    pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
  });

  const close = screen.getByRole("button", { name: "Dismiss: Delete session failed" });
  const focusSpy = jest.spyOn(HTMLElement.prototype, "focus");
  try {
    // A mouse dismissal with focus parked elsewhere entirely: moving it would
    // be the toast reaching into a surface it was never part of. `fireEvent`
    // for the same reason as above.
    fireEvent.click(close);
    expect(focusSpy).not.toHaveBeenCalled();
  } finally {
    focusSpy.mockRestore();
  }
});

describe("S5.6 — UX12, answered and closed", () => {
  function TrappedDialog({ onClose }: { onClose: () => void }) {
    const ref = useDialogFocusTrap<HTMLDivElement>();
    return (
      <div role="dialog" aria-modal="true" ref={ref}>
        <button type="button" onClick={onClose}>
          Confirm delete
        </button>
      </div>
    );
  }

  function WithDialog() {
    const [open, setOpen] = useState(true);
    return (
      <>
        <button type="button">Delete chat</button>
        {open ? <TrappedDialog onClose={() => setOpen(false)} /> : null}
        <ToastHost />
      </>
    );
  }

  it("a toast raised over a trapped dialog is still there, and reachable, once it closes", async () => {
    const user = userEvent.setup();
    render(<WithDialog />);

    act(() => {
      pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
    });

    // While the dialog is up its trap owns Tab, and the toast's controls are
    // outside it. That is accepted (none of the 11 consumers is changed); what
    // makes the criterion true is the toast still being there afterwards.
    await user.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();

    act(() => trigger().focus());
    await user.tab();
    expect(screen.getByRole("button", { name: "Copy message: Delete session failed" })).toHaveFocus();
  });
});
