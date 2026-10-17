import { AnimatePresence, m } from "motion/react";
import { useCallback, useEffect, useRef, useState, type FocusEvent } from "react";

import { useAutoDismiss } from "../hooks/useAutoDismiss";
import { DURATION, EASE_OUT } from "../lib/motionTokens";
import { toastCopyWhat, toastDismissLabel } from "../lib/toastControlNames";

import { type Toast, useToastStore } from "../state/toastStore";
import { CopyValueButton } from "./CopyValueButton";
import { IconCloseButton } from "./IconCloseButton";
import "./ToastHost.css";

const TONE_GLYPH: Record<Toast["tone"], string> = {
  error: "✕",
  warning: "⚠",
  success: "✓",
  info: "ℹ",
};

/**
 * One toast, and the two things that make its message usable.
 *
 * The deadline spends only un-interacted time (`useAutoDismiss`), and the copy
 * control hands the whole message over verbatim — the message is clipped to
 * four lines on purpose, so the clipboard, not a wider toast, is the route to a
 * refusal's 86-character worktree path.
 *
 * Accepted limit (UX14): with the stack already at `MAX_TOASTS`, a clipboard
 * refusal's own toast can evict the oldest toast, which may be the one being
 * copied from. The cap and its drop-oldest order are deliberate and there is no
 * exemption for the toast under the pointer — the newest failure is the one
 * tied to what the operator just did, and a pointer-dependent eviction order
 * would be a new rule nobody asked for.
 *
 * Both control names arrive from the parent. The ambiguity they resolve is a
 * property of the whole stack (UX-D1) and a toast cannot see the stack.
 */
function ToastItem({
  toast,
  copyWhat,
  dismissLabel,
  onDismiss,
}: {
  toast: Toast;
  copyWhat: string;
  dismissLabel: string;
  onDismiss: (id: string) => void;
}) {
  const { id, duration, revision } = toast;
  const root = useRef<HTMLDivElement | null>(null);
  /**
   * Whatever held focus when focus FIRST arrived from outside this toast.
   *
   * Never overwritten: a keyboard operator who steps out to another control and
   * back would otherwise be returned to that detour rather than to the place
   * they were working. Mirrors `useDialogFocusTrap`'s opener handling.
   */
  const opener = useRef<HTMLElement | null>(null);

  const dismiss = useCallback(() => onDismiss(id), [onDismiss, id]);
  const autoDismiss = useAutoDismiss({ duration, revision, onDismiss: dismiss });

  const handleFocus = (event: FocusEvent<HTMLDivElement>) => {
    autoDismiss.onFocus();
    if (opener.current !== null) return;
    const from = event.relatedTarget as HTMLElement | null;
    if (from && !event.currentTarget.contains(from)) opener.current = from;
  };

  /**
   * Dismissal from the toast's own close button, which is the only route that
   * moves focus. A timer, `clear()` and an eviction all leave focus alone: the
   * operator is somewhere else, and reaching into that surface is not this
   * component's business.
   */
  const handleClose = () => {
    const from = opener.current;
    const hadFocus = root.current !== null && root.current.contains(document.activeElement);
    onDismiss(id);
    if (hadFocus && from !== null && document.contains(from)) from.focus();
  };

  return (
    <m.div
      ref={root}
      className={`toast toast--${toast.tone}`}
      role={toast.tone === "error" || toast.tone === "warning" ? "alert" : "status"}
      initial={{ opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0, transition: { duration: DURATION.med, ease: EASE_OUT } }}
      exit={{ opacity: 0, x: 16, transition: { duration: DURATION.fast, ease: EASE_OUT } }}
      onPointerEnter={autoDismiss.onPointerEnter}
      onPointerLeave={autoDismiss.onPointerLeave}
      onFocus={handleFocus}
      onBlur={autoDismiss.onBlur}
    >
      <span className="toast__glyph" aria-hidden>
        {TONE_GLYPH[toast.tone]}
      </span>
      <div className="toast__body">
        <div className="toast__title">{toast.title}</div>
        {toast.message ? <div className="toast__message">{toast.message}</div> : null}
      </div>
      {/* No message, no control: a disabled button is a promise the surface
          cannot keep, and it costs a tab stop to say so. */}
      {toast.message ? (
        <CopyValueButton value={toast.message} what={copyWhat} />
      ) : null}
      <IconCloseButton onClick={handleClose} aria-label={dismissLabel} />
    </m.div>
  );
}

const EDGE_GAP = 20;

/**
 * Corner of the page area, not of the window.
 *
 * The utility dock is a real flex sibling — bottom edge or right edge, and its
 * size moves with the Copilot panel. Measuring the screen area keeps a toast
 * off the dock's chat composer wherever the operator has parked it, without
 * this component having to know the dock's layout rules.
 */
function useScreenAreaCorner() {
  const [corner, setCorner] = useState({ right: EDGE_GAP, bottom: EDGE_GAP });

  useEffect(() => {
    const area = document.querySelector(".screen-area");
    if (!area) return;

    const measure = () => {
      const rect = area.getBoundingClientRect();
      setCorner({
        right: Math.max(EDGE_GAP, window.innerWidth - rect.right + EDGE_GAP),
        bottom: Math.max(EDGE_GAP, window.innerHeight - rect.bottom + EDGE_GAP),
      });
    };
    measure();

    window.addEventListener("resize", measure);
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(area);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, []);

  return corner;
}

/**
 * App-wide toast stack.
 *
 * Mounted once in AppLayout. Anything can push to it — including code outside
 * React, via `pushToast` — so a failed action reports itself even when the
 * component that started it has gone away.
 *
 * The host stays mounted while empty: a dismissed toast needs somewhere to
 * play its exit, and a live region that already exists is announced more
 * reliably than one inserted together with its first message.
 */
export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  const corner = useScreenAreaCorner();

  return (
    <div className="toast-host" aria-live="polite" style={corner}>
      <AnimatePresence initial={false}>
        {/* ux-ok: no toasts is the resting state and should draw nothing; the stack is a local store, not a load that can fail */}
        {toasts.map((toast) => (
          <ToastItem
            key={toast.id}
            toast={toast}
            copyWhat={toastCopyWhat(toast, toasts)}
            dismissLabel={toastDismissLabel(toast, toasts)}
            onDismiss={dismiss}
          />
        ))}
      </AnimatePresence>
    </div>
  );
}
