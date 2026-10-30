import { useCallback, useEffect, useRef } from "react";

export interface AutoDismissOptions {
  /** ms the toast gets on screen; 0 or less schedules nothing (sticky). */
  duration: number;
  /**
   * Bumped by the store whenever the same toast is raised again. The deadline
   * is keyed on it, so a repeat failure gets a fresh clock while React keeps
   * the same DOM node — the node is not remounted under the operator's pointer
   * mid-selection, which is the whole reason the id stays stable.
   */
  revision: number;
  onDismiss: () => void;
}

/** The handler props the interacting element spreads onto itself. */
export interface AutoDismissHandlers {
  onPointerEnter: () => void;
  onPointerLeave: () => void;
  onFocus: () => void;
  onBlur: () => void;
}

/**
 * A dismissal deadline that spends only the time nobody is interacting.
 *
 * A fixed `setTimeout` is a WCAG 2.2.1 (Timing Adjustable, Level A) failure for
 * content the operator has to read and act on: a refusal naming an 86-character
 * worktree path cannot be selected, read and used inside nine seconds, and the
 * clock used to keep running with the mouse sitting on the message.
 *
 * So the deadline suspends while the pointer is over the element or focus is
 * inside it, and resumes from the REMAINING time rather than restarting —
 * restarting would hand an operator who brushes past a 9-second toast fourteen
 * seconds, and one who keeps brushing a toast that never leaves.
 *
 * Pointer and focus are read from the element's own React props (React's
 * `onFocus`/`onBlur` are delegated `focusin`/`focusout` and bubble, so a
 * handler on a toast root sees focus entering and leaving its buttons). No
 * document-level listener and no ref-walking: the element that spreads these is
 * the element the deadline is about.
 */
export function useAutoDismiss({
  duration,
  revision,
  onDismiss,
}: AutoDismissOptions): AutoDismissHandlers {
  // The callback is read through a ref rather than depended on. `ToastItem`
  // rebuilds it on every render (it closes over the toast's id), and a
  // dependency on its identity would re-arm the timer on every unrelated
  // re-render — the opposite sign of this ticket's defect, a toast that never
  // leaves.
  const dismiss = useRef(onDismiss);
  useEffect(() => {
    dismiss.current = onDismiss;
  }, [onDismiss]);

  const pointerOver = useRef(false);
  const focusWithin = useRef(false);
  const remaining = useRef(duration);
  const armedAt = useRef(0);
  const timer = useRef<number | null>(null);

  const clear = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const arm = useCallback((ms: number) => {
    armedAt.current = Date.now();
    timer.current = window.setTimeout(() => {
      timer.current = null;
      dismiss.current();
    }, ms);
  }, []);

  /**
   * Bank the time already spent and stop the clock.
   *
   * Guarded on the timer rather than on the flags: pointer and focus events do
   * not arrive in pairs (a toast raised under a resting mouse gets a leave with
   * no enter), and `remaining -= elapsed` run twice would spend the interval
   * twice.
   */
  const suspend = useCallback(() => {
    if (timer.current === null) return;
    remaining.current = Math.max(0, remaining.current - (Date.now() - armedAt.current));
    clear();
  }, [clear]);

  const resume = useCallback(() => {
    if (pointerOver.current || focusWithin.current) return;
    if (duration <= 0 || timer.current !== null) return;
    arm(remaining.current);
  }, [arm, duration]);

  // A new revision (or a changed duration) is a new deadline, and deliberately
  // does NOT reset the interaction state: a repeat failure arriving while the
  // operator's pointer is on the toast must not start the clock under it.
  useEffect(() => {
    clear();
    remaining.current = duration;
    if (duration > 0 && !pointerOver.current && !focusWithin.current) arm(duration);
    return clear;
  }, [duration, revision, arm, clear]);

  const onPointerEnter = useCallback(() => {
    pointerOver.current = true;
    suspend();
  }, [suspend]);

  const onPointerLeave = useCallback(() => {
    pointerOver.current = false;
    resume();
  }, [resume]);

  const onFocus = useCallback(() => {
    focusWithin.current = true;
    suspend();
  }, [suspend]);

  const onBlur = useCallback(() => {
    focusWithin.current = false;
    resume();
  }, [resume]);

  return { onPointerEnter, onPointerLeave, onFocus, onBlur };
}
