import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LazyMotion, MotionConfig, domAnimation } from "motion/react";
import { useState } from "react";

import { Button } from "../Button";
import { ModalShell } from "../ModalShell";

/** A trigger and the dialog it opens, under the app's motion providers. */
function Harness({ onConfirm = jest.fn() }: { onConfirm?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <Button variant="secondary" onClick={() => setOpen(true)}>
          Open settings
        </Button>
        <ModalShell open={open} onDismiss={() => setOpen(false)} labelledBy="shell-title">
          <h2 id="shell-title">Settings</h2>
          <Button
            variant="primary"
            onClick={() => {
              onConfirm();
              setOpen(false);
            }}
          >
            Save
          </Button>
        </ModalShell>
      </MotionConfig>
    </LazyMotion>
  );
}

it("opens a named, modal dialog and moves focus into it", async () => {
  const user = userEvent.setup();
  render(<Harness />);

  await user.click(screen.getByRole("button", { name: "Open settings" }));

  const dialog = screen.getByRole("dialog", { name: "Settings" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
});

it("leaves the DOM after Escape once its exit has played, and focus returns to the trigger", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "Open settings" });

  await user.click(trigger);
  await user.keyboard("{Escape}");

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
});

it("closes on the backdrop", async () => {
  const user = userEvent.setup();
  const { container } = render(<Harness />);

  await user.click(screen.getByRole("button", { name: "Open settings" }));
  const backdrop = container.querySelector(".modal-overlay");
  if (!backdrop) throw new Error("no backdrop");
  await user.click(backdrop);

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("during its exit takes no input and holds no focus", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const trigger = screen.getByRole("button", { name: "Open settings" });

  await user.click(trigger);
  // A bare click, not user-event's: user-event awaits past the exit, and the
  // state that matters is the one between the click and the unmount.
  act(() => screen.getByRole("button", { name: "Save" }).click());

  // Still on screen while it leaves: inert (no focus, no clicks in a browser),
  // pointer events off, and focus already back on the trigger.
  const leaving = document.querySelector<HTMLElement>(".modal-panel");
  expect(leaving).not.toBeNull();
  expect(leaving).toHaveAttribute("inert");
  expect(leaving).toHaveAttribute("aria-hidden", "true");
  expect(leaving?.style.pointerEvents).toBe("none");
  expect(trigger).toHaveFocus();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("cannot be dismissed while onDismiss is undefined", async () => {
  const user = userEvent.setup();
  render(
    <ModalShell open onDismiss={undefined} labelledBy="busy-title">
      <h2 id="busy-title">Saving</h2>
    </ModalShell>,
  );

  await user.keyboard("{Escape}");

  expect(screen.getByRole("dialog", { name: "Saving" })).toBeInTheDocument();
});
