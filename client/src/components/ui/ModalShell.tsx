import { AnimatePresence, m, useIsPresent } from "motion/react";
import { useLayoutEffect, type ReactNode } from "react";

import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";
import { DURATION, EASE_OUT } from "../../lib/motionTokens";

interface ModalShellProps {
  /** Whether the dialog is up. The shell stays mounted so a close can play its exit. */
  open: boolean;
  /** What Escape and the backdrop do; `undefined` while the dialog must not be dismissed (a save in flight). */
  onDismiss: (() => void) | undefined;
  /** Id of the element that names the dialog. */
  labelledBy: string;
  /** Extra classes for the panel, e.g. `modal-panel-wide`. */
  panelClassName?: string;
  children: ReactNode;
}

/**
 * The overlay, panel, focus trap and Escape for a dialog, with an enter and an exit.
 *
 * A dialog that unmounts itself on close vanishes in one frame; this one stays
 * mounted for the length of its exit. `.modal-panel` centres itself with
 * `transform`, which Motion writes inline, so the centring is carried as
 * Motion's own `x`/`y` percentages and the entrance moves `y` by 2% of the
 * panel's height. Reduced motion is the root `MotionConfig`'s job.
 */
export function ModalShell({ open, onDismiss, labelledBy, panelClassName, children }: ModalShellProps) {
  // Registered from the shell, not the panel: a closed dialog stops claiming
  // Escape at once, not when its exit finishes.
  useDialogDismiss(open ? onDismiss : null);

  return (
    <AnimatePresence>
      {open ? (
        <ModalShellContent
          key="modal"
          onDismiss={onDismiss}
          labelledBy={labelledBy}
          panelClassName={panelClassName}
        >
          {children}
        </ModalShellContent>
      ) : null}
    </AnimatePresence>
  );
}

function ModalShellContent({
  onDismiss,
  labelledBy,
  panelClassName,
  children,
}: Omit<ModalShellProps, "open">) {
  // False for the length of the exit. A leaving dialog lets go of focus (the
  // trap's teardown hands it back to the opener) and takes no input.
  const isPresent = useIsPresent();
  const trapRef = useDialogFocusTrap<HTMLDivElement>();
  // Set in this render, not by the exit animation, which applies on a later frame.
  const leaving = isPresent ? undefined : ({ pointerEvents: "none" } as const);

  // Release the trap the moment the exit starts, so focus goes back to the
  // opener now rather than when the panel unmounts. Swapping the ref does not
  // do it: Motion composes the ref it is given and never detaches the old one.
  useLayoutEffect(() => {
    if (!isPresent) trapRef(null);
  }, [isPresent, trapRef]);

  return (
    <>
      <m.div
        className="modal-overlay modal-overlay--shell"
        onClick={isPresent ? onDismiss : undefined}
        role="presentation"
        inert={!isPresent}
        style={leaving}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1, transition: { duration: DURATION.slow, ease: EASE_OUT } }}
        exit={{
          opacity: 0,
          transition: { duration: DURATION.med, ease: EASE_OUT },
        }}
      />
      <m.div
        ref={trapRef}
        className={["modal-panel", "modal-panel--shell", panelClassName].filter(Boolean).join(" ")}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        inert={!isPresent}
        style={leaving}
        initial={{ opacity: 0, x: "-50%", y: "-48%", scale: 0.98 }}
        animate={{
          opacity: 1,
          x: "-50%",
          y: "-50%",
          scale: 1,
          transition: { duration: DURATION.slow, ease: EASE_OUT },
        }}
        exit={{
          opacity: 0,
          x: "-50%",
          y: "-48%",
          scale: 0.98,
          transition: { duration: DURATION.med, ease: EASE_OUT },
        }}
      >
        {children}
      </m.div>
    </>
  );
}
