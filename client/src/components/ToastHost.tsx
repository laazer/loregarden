import { AnimatePresence, m } from "motion/react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { IS_DEV } from "../api/viteEnv";
import { copyText } from "../lib/clipboard";
import { DURATION, EASE_OUT } from "../lib/motionTokens";
import { splitMessageForWrap } from "../lib/toastWrap";

import { type Toast, pushToast, useToastStore } from "../state/toastStore";
import { IconCloseButton } from "./IconCloseButton";
import { Button } from "./ui/Button";
import "./ToastHost.css";

declare global {
  interface Window {
    /** Dev-build-only handle on `pushToast`, for driving a toast from Playwright. */
    __loregardenPushToast?: typeof pushToast;
  }
}

const TONE_GLYPH: Record<Toast["tone"], string> = {
  error: "✕",
  warning: "⚠",
  success: "✓",
  info: "ℹ",
};

/**
 * Past this, a message gets the recovery controls and the clamp modifier.
 *
 * Derived from the clamp itself: 4 lines at roughly 50 characters per line in
 * the real 292px line box. Set deliberately below the shortest message class
 * observed to clip (220), so the band 160-219 may carry controls on a message
 * that does not actually reach the fourth line — "Show all" is then a no-op
 * toggle and "Copy message" still earns its place. The error leans toward
 * offering recovery, never toward hiding it.
 *
 * Not a layout measurement: jsdom reports `scrollHeight` and `clientHeight` as
 * 0, so `scrollHeight > clientHeight` would read "not clamped" in every Jest
 * assertion and make the visible-cut and no-noise rules untestable. Precedent:
 * `LONG_CHECKLIST_ITEM_CHARS` in `ApprovalCard.tsx`.
 */
const LONG_TOAST_MESSAGE_CHARS = 160;

/** How long the copy control reads "Copied" before reverting. */
const COPY_FEEDBACK_MS = 1800;

/**
 * Floor on a resumed dismiss timer, so a toast never vanishes the instant the
 * pointer leaves it. It applies on every resume, so repeated hovering keeps a
 * toast alive for as long as the operator is reading — intended.
 */
const RESUME_FLOOR_MS = 3000;

type CopyFeedback = "idle" | "copied" | "failed";

/**
 * The message, with each path segment made unbreakable.
 *
 * A `/`-separated segment of a path token becomes a `.toast__path-part` span —
 * `white-space: nowrap` is the one declaration that suppresses the hyphen
 * soft-wrap inside a worktree directory name, and it needs an element to hang
 * off. Prose renders as a bare text node, so it keeps wrapping at its own
 * spaces and the common case gains nothing. A `<wbr/>` goes between two
 * segments of the same token and nowhere else: a break opportunity after every
 * separator, and none dangling off the end of a path.
 *
 * A span and a `<wbr>` both contribute no text, so the node's `textContent`
 * stays byte-identical to `message`. Derived from `message` alone, so the
 * children are the same collapsed and expanded — expanding flips a className
 * and never touches the node inside `role="alert"`, which is what keeps it from
 * re-announcing.
 */
function useWrappedMessage(message: string): ReactNode[] {
  return useMemo(() => {
    const parts = splitMessageForWrap(message);
    const children: ReactNode[] = [];
    parts.forEach((part, index) => {
      if (!part.nowrap) {
        children.push(part.text);
        return;
      }
      const previous = parts[index - 1];
      if (previous?.nowrap && previous.text.endsWith("/")) {
        children.push(<wbr key={`wbr-${index}`} />);
      }
      children.push(
        <span key={`part-${index}`} className="toast__path-part">
          {part.text}
        </span>,
      );
    });
    return children;
  }, [message]);
}

/**
 * The per-toast dismiss timer, pausable.
 *
 * `DEFAULT_DURATION.error` is 9000ms and a handoff refusal is 269 characters, so
 * the budget and the message length disagree. Pausing rather than pushing the
 * toast sticky: "does this message carry a path" is not a judgement the store
 * should make, the store's positional eviction would drop a sticky toast
 * anyway, and a pause fixes every long toast rather than only path-shaped ones.
 *
 * A toast is never left with both a running timer and a pause reason
 * outstanding, and the full duration is never restarted from scratch.
 */
function useDismissTimer(duration: number, onFire: () => void) {
  const timerRef = useRef<number | null>(null);
  const deadlineRef = useRef(0);
  const remainingRef = useRef(duration);
  const reasonsRef = useRef<Set<string>>(new Set());
  const fireRef = useRef(onFire);
  fireRef.current = onFire;

  const clear = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const start = useCallback(
    (ms: number) => {
      clear();
      deadlineRef.current = Date.now() + ms;
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        fireRef.current();
      }, ms);
    },
    [clear],
  );

  useEffect(() => {
    if (duration <= 0) return;
    remainingRef.current = duration;
    start(duration);
    return clear;
  }, [duration, start, clear]);

  const pause = useCallback(
    (reason: string) => {
      if (duration <= 0) return;
      const reasons = reasonsRef.current;
      const first = reasons.size === 0;
      reasons.add(reason);
      if (first && timerRef.current !== null) {
        remainingRef.current = Math.max(0, deadlineRef.current - Date.now());
        clear();
      }
    },
    [duration, clear],
  );

  const resume = useCallback(
    (reason: string) => {
      if (duration <= 0) return;
      const reasons = reasonsRef.current;
      if (!reasons.delete(reason)) return;
      if (reasons.size > 0) return;
      start(Math.max(RESUME_FLOOR_MS, remainingRef.current));
    },
    [duration, start],
  );

  return { pause, resume };
}

interface ToastItemProps {
  toast: Toast;
  onDismiss: (id: string) => void;
}

function ToastItem({ toast, onDismiss }: ToastItemProps) {
  const { id, duration, message } = toast;
  const [expanded, setExpanded] = useState(false);
  const [copying, setCopying] = useState(false);
  const [feedback, setFeedback] = useState<CopyFeedback>("idle");
  const feedbackTimer = useRef<number | null>(null);
  const nodeRef = useRef<HTMLDivElement | null>(null);
  const copyButtonRef = useRef<HTMLButtonElement | null>(null);
  const refocusCopyRef = useRef(false);

  const children = useWrappedMessage(message);
  const recoverable = message.length > LONG_TOAST_MESSAGE_CHARS;
  const clamped = recoverable && !expanded;

  const dismiss = useCallback(() => onDismiss(id), [id, onDismiss]);
  const { pause, resume } = useDismissTimer(duration, dismiss);

  // Expansion is a pause reason in its own right: the operator is reading.
  useEffect(() => {
    if (expanded) pause("expanded");
    else resume("expanded");
  }, [expanded, pause, resume]);

  useEffect(() => {
    return () => {
      if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
    };
  }, []);

  const announceCopy = useCallback((state: Exclude<CopyFeedback, "idle">) => {
    setFeedback(state);
    if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
    feedbackTimer.current = null;
    // A failure stays on screen: it is the only thing telling the operator the
    // path is still only selectable by hand.
    if (state === "copied") {
      feedbackTimer.current = window.setTimeout(() => {
        feedbackTimer.current = null;
        setFeedback("idle");
      }, COPY_FEEDBACK_MS);
    }
  }, []);

  const handleCopy = useCallback(() => {
    if (copying) return;
    refocusCopyRef.current = document.activeElement === copyButtonRef.current;
    setCopying(true);
    // The store's string, never the DOM's: the clamp hides text visually only,
    // so a `textContent`-derived copy risks shipping the truncated path.
    copyText(message)
      .then(() => announceCopy("copied"))
      .catch(() => {
        // Surfaced, not swallowed: the label and the live region both say so,
        // and the message auto-expands so the text can be selected by hand.
        announceCopy("failed");
        setExpanded(true);
      })
      .finally(() => setCopying(false));
  }, [copying, message, announceCopy]);

  /**
   * Give the copy control its focus back once the write settles.
   *
   * Disabling a focused button blurs it to `<body>` in a real browser, which
   * drops the keyboard operator out of the toast in the middle of recovering
   * the path: Escape stops dismissing, Tab restarts at the top of the page, and
   * the focus-based timer pause is released under someone who is still reading.
   * jsdom does not move focus on disable, so this is only reachable in a
   * browser — measured in Chromium on 2026-10-20.
   */
  useEffect(() => {
    if (copying || !refocusCopyRef.current) return;
    refocusCopyRef.current = false;
    if (nodeRef.current?.contains(document.activeElement)) return;
    copyButtonRef.current?.focus();
  }, [copying]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      dismiss();
    },
    [dismiss],
  );

  const handleBlur = useCallback(
    (event: React.FocusEvent<HTMLDivElement>) => {
      const next = event.relatedTarget;
      if (next instanceof Node && nodeRef.current?.contains(next)) return;
      resume("focus");
    },
    [resume],
  );

  const copyLabel = copying || feedback === "idle" ? "Copy message" : feedback === "copied" ? "Copied" : "Copy failed";

  return (
    <m.div
      ref={nodeRef}
      className={`toast toast--${toast.tone}`}
      data-toast-id={id}
      role={toast.tone === "error" || toast.tone === "warning" ? "alert" : "status"}
      aria-keyshortcuts="Alt+T"
      onMouseEnter={() => pause("hover")}
      onMouseLeave={() => resume("hover")}
      onFocus={() => pause("focus")}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
      initial={{ opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0, transition: { duration: DURATION.med, ease: EASE_OUT } }}
      exit={{ opacity: 0, x: 16, transition: { duration: DURATION.fast, ease: EASE_OUT } }}
    >
      <span className="toast__glyph" aria-hidden>
        {TONE_GLYPH[toast.tone]}
      </span>
      <div className="toast__body">
        <div className="toast__title">{toast.title}</div>
        {message ? (
          <div
            className={`toast__message${clamped ? " toast__message--clamped" : ""}`}
            // Expanded, the block scrolls, so its scroll container has to be
            // keyboard-reachable. No aria-label in either state: the message's
            // own text is its name, and a label would shadow the alert text.
            tabIndex={expanded ? 0 : undefined}
          >
            {children}
          </div>
        ) : null}
        {recoverable ? (
          <div className="toast__actions">
            <Button
              variant="plain"
              className="approval-inline-toggle"
              aria-expanded={expanded}
              onClick={() => setExpanded((open) => !open)}
            >
              {expanded ? "Show less" : "Show all"}
            </Button>
            <Button
              ref={copyButtonRef}
              variant="plain"
              className="approval-inline-toggle"
              disabled={copying}
              onClick={handleCopy}
            >
              {copyLabel}
            </Button>
            {/* Rendered only while feedback is live, so a resting info or
                success toast keeps exactly one `status` node — its own. */}
            {feedback === "idle" ? null : (
              <span role="status" className="toast__feedback">
                {feedback === "copied" ? "Message copied" : "Message copy failed"}
              </span>
            )}
          </div>
        ) : null}
      </div>
      <IconCloseButton onClick={dismiss} aria-label={`Dismiss: ${toast.title}`} />
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
 *
 * Alt+T moves focus to the newest toast's first control. The stack mounts last
 * in AppLayout and an error toast dismisses in 9s, so Tab-reaching it is a race
 * even with the timer pause — a named control in an unreachable region is a
 * keyboard route in name only.
 */
export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  const corner = useScreenAreaCorner();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<{ element: HTMLElement; toastId: string } | null>(null);

  useEffect(() => {
    if (!IS_DEV) return;
    // Dev-only harness: the only way to render an exact 269-character message
    // in a real browser. Never read by app code, and inert in a production
    // build, where IS_DEV is false.
    window.__loregardenPushToast = pushToast;
    return () => {
      delete window.__loregardenPushToast;
    };
  }, []);

  const handleDismiss = useCallback(
    (id: string) => {
      const restore = restoreRef.current;
      const active = document.activeElement;
      const node = hostRef.current?.querySelector(`[data-toast-id="${id}"]`);
      const focusWasInside = active instanceof Node && node ? node.contains(active) : false;

      dismiss(id);

      if (!restore || restore.toastId !== id) return;
      restoreRef.current = null;
      // Only take focus back from the toast that is going away, and only to an
      // element still in the document: a detached node cannot be read out,
      // operated, or tabbed away from.
      if (focusWasInside && restore.element.isConnected) restore.element.focus();
    },
    [dismiss],
  );

  /**
   * Hand focus back when it leaves the stack to nowhere.
   *
   * Alt+T is a one-way trip otherwise. ToastHost mounts second-to-last in
   * AppLayout, so a toast's Dismiss button is at or near the last tabbable
   * element in the document: an operator who takes Alt+T in, tabs Show all ->
   * Copy message -> Dismiss and tabs once more lands on `document.body` with
   * no route back to where they were, and the stored element is then dropped
   * silently when the toast is dismissed.
   *
   * Only when focus went nowhere. A `relatedTarget` that is a real element
   * means the operator chose where to go, and a detached stored element cannot
   * be read out, operated, or tabbed away from.
   */
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const onFocusOut = (event: FocusEvent) => {
      const next = event.relatedTarget;
      if (next !== null && next !== document.body) return;

      const restore = restoreRef.current;
      if (!restore) return;
      // The dismiss path owns the case where the toast is going away.
      if (!host.querySelector(`[data-toast-id="${restore.toastId}"]`)) return;

      restoreRef.current = null;
      if (restore.element.isConnected) restore.element.focus();
    };

    host.addEventListener("focusout", onFocusOut);
    return () => host.removeEventListener("focusout", onFocusOut);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      // `event.code`, not `event.key`: on macOS Option+T yields key "†", so a
      // key-based binding answers to the glyph the layout produced rather than
      // the key the operator pressed.
      if (event.code !== "KeyT") return;
      if (!event.altKey || event.ctrlKey || event.metaKey) return;

      const host = hostRef.current;
      if (!host) return;
      const stack = host.querySelectorAll<HTMLElement>(".toast");
      const newest = stack[stack.length - 1];
      const target = newest?.querySelector<HTMLButtonElement>("button");
      const toastId = newest?.dataset.toastId;
      if (!target || !toastId) return;

      event.preventDefault();
      const active = document.activeElement;
      restoreRef.current =
        active instanceof HTMLElement && !host.contains(active)
          ? { element: active, toastId }
          : null;
      target.focus();
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="toast-host" aria-live="polite" style={corner} ref={hostRef}>
      <AnimatePresence initial={false}>
        {/* ux-ok: no toasts is the resting state and should draw nothing; the stack is a local store, not a load that can fail */}
        {toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} onDismiss={handleDismiss} />
        ))}
      </AnimatePresence>
    </div>
  );
}
