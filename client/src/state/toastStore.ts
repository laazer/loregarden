import { create } from "zustand";

// Imported from `api/http`, where the class is defined, rather than through the
// `api/client` barrel: a test that mocks the barrel leaves `ApiError` undefined
// and `describeError` throws inside the failure it was called to describe.
import { ApiError } from "../api/http";

export type ToastTone = "error" | "warning" | "success" | "info";

export interface Toast {
  id: string;
  tone: ToastTone;
  title: string;
  message: string;
  /** ms on screen before auto-dismiss; 0 keeps it until dismissed. */
  duration: number;
  /**
   * Bumped every time this failure is raised again. A repeat keeps its `id`
   * so React keeps the DOM node — the node must not be remounted under the
   * operator's pointer mid-selection — and `useAutoDismiss` keys its deadline
   * on this instead, so the toast still says "still happening" with a fresh
   * clock. Taken from the monotonic `seq`, never a clock or a random, so the
   * arithmetic is exact under fake timers.
   */
  revision: number;
  /**
   * The live claims on this toast, used as a set — every entry is a handle
   * some caller is still holding and will release.
   *
   * A toast's identity is its `id`; a caller's handle is a claim, and the two
   * vocabularies are disjoint (`toast-` / `claim-`). Only `pushClaimed` mints
   * one, so a fire-and-forget push leaves this empty: a claim nobody holds is
   * a claim nothing can ever release, and 81 of the tree's 82 push sites
   * discard what they are handed. `release` drops one claim and takes the
   * toast only when the set empties; `dismiss` takes the toast outright,
   * claims and all.
   */
  claims: string[];
}

export interface ToastInput {
  tone?: ToastTone;
  title: string;
  message?: string;
  duration?: number;
}

/**
 * An error toast is sticky, and that is a WCAG 2.2.1 (Timing Adjustable, Level
 * A) requirement rather than a preference: a bounded timer on content the
 * operator has to read and act on — a refusal naming an 86-character worktree
 * path they have to get to — fails it, and pause-on-hover mitigates it only for
 * someone holding a mouse. A caller with a failure that carries nothing to copy
 * and re-reports itself can still ask for a bounded one.
 */
const DEFAULT_DURATION: Record<ToastTone, number> = {
  error: 0,
  warning: 7000,
  success: 4000,
  info: 5000,
};

/**
 * Cap the stack. A failing poll or a bulk action can fail many times in a row,
 * and a wall of toasts hides the app instead of reporting on it.
 */
const MAX_TOASTS = 4;

let seq = 0;

/**
 * A claim id, from the same monotonic `seq` that mints toast ids and
 * `revision` — never a clock and never `Math.random()`, so the arithmetic
 * stays exact under fake timers. The `claim-` prefix keeps the two
 * vocabularies disjoint: no claim can ever be mistaken for a toast id.
 */
function mintClaim(): string {
  return `claim-${++seq}`;
}

interface ToastState {
  toasts: Toast[];
  /**
   * Raise or refresh a toast and walk away, minting NO claim: returns the
   * toast's own id (the deduped-onto toast's id on a repeat). The route 81 of
   * the tree's 82 push sites take, every one of which discards the return
   * value.
   */
  push: (input: ToastInput) => string;
  /**
   * Raise or refresh a toast and keep a handle on it, for a caller that will
   * release when its action settles: returns THIS CALL's claim id.
   *
   * A second function rather than a `{ claimed: true }` option on `push`,
   * deliberately: an option would make the MEANING of the returned string
   * depend on an argument value, which TypeScript can only express with
   * overloads and which no call site asked for.
   */
  pushClaimed: (input: ToastInput) => string;
  /** Give up one claim; removes the toast only when its last claim goes. */
  release: (claimId: string) => void;
  /** Take the toast away now, claims and all — the operator's route. */
  dismiss: (id: string) => void;
  clear: () => void;
}

export const useToastStore = create<ToastState>((set, get) => {
  /**
   * The one raise path both routes take. `claim` is the claim this call
   * retains, or `null` for a fire-and-forget push, which mints nothing.
   * Returns the id of the toast the push landed on.
   */
  const raise = (input: ToastInput, claim: string | null): string => {
    const tone = input.tone ?? "error";
    const raised = {
      tone,
      title: input.title,
      message: input.message ?? "",
      duration: input.duration ?? DEFAULT_DURATION[tone],
      revision: ++seq,
    };

    // A repeated failure refreshes its twin IN PLACE rather than stacking or
    // being re-raised with a new id: same id, same index, same DOM node — only
    // `revision` and `duration` move. A new id would remount the node, taking
    // the message out from under a pointer mid-selection, which is the defect
    // the dedupe was meant to help with.
    const existing = get().toasts.find(
      (toast) =>
        toast.tone === raised.tone &&
        toast.title === raised.title &&
        toast.message === raised.message,
    );
    if (existing) {
      set((state) => ({
        toasts: state.toasts.map((toast) =>
          toast.id === existing.id
            ? {
                ...toast,
                ...raised,
                // `claims` is set explicitly AFTER the spread, and is
                // deliberately not a field of `raised`: letting the refreshed
                // bundle carry the claim set would reset it to this call's
                // claim alone, and the first caller to settle would again take
                // a toast its twin is still relying on.
                //
                // A fire-and-forget repeat passes the SAME array through
                // rather than copying it. That is what makes the cost linear:
                // the unbounded growth this ticket fixes was the dedupe branch
                // rebuilding a claim array that grew by one per server-driven
                // event, so N events cost O(N²) and nothing ever released one.
                // The array is never mutated in place — it is state a zustand
                // selector has already handed to a renderer.
                claims: claim === null ? toast.claims : [...toast.claims, claim],
              }
            : toast,
        ),
      }));
      return existing.id;
    }

    const toast: Toast = {
      id: `toast-${++seq}`,
      ...raised,
      // A fresh array per toast, never a shared empty constant: two toasts
      // holding one claim set is one in-place append away from every unclaimed
      // toast in the stack carrying the next claim minted anywhere.
      claims: claim === null ? [] : [claim],
    };
    set((state) => ({ toasts: [...state.toasts, toast].slice(-MAX_TOASTS) }));
    return toast.id;
  };

  return {
    toasts: [],
    push: (input) => raise(input, null),
    pushClaimed: (input) => {
      const claim = mintClaim();
      raise(input, claim);
      return claim;
    },
    release: (claimId) =>
      set((state) => {
        // Only ever maps or filters the EXISTING array, so a claim held past
        // its toast's removal — by the close button, by a `MAX_TOASTS`
        // eviction, or by `clear()` — cannot resurrect it, and a claim is
        // looked up by the toast HOLDING it rather than by its dedupe key or
        // by any other field, so it cannot take a byte-identical twin raised
        // after it, and cannot take a toast that holds no claims at all.
        const holder = state.toasts.find((toast) => toast.claims.includes(claimId));
        if (!holder) return state;

        const remaining = holder.claims.filter((claim) => claim !== claimId);
        if (remaining.length === 0) {
          return { toasts: state.toasts.filter((toast) => toast.id !== holder.id) };
        }
        return {
          toasts: state.toasts.map((toast) =>
            toast.id === holder.id ? { ...toast, claims: remaining } : toast,
          ),
        };
      }),
    dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
    clear: () => set({ toasts: [] }),
  };
});

/**
 * Push a toast from outside React (mutation cache, plain catch blocks) and
 * walk away. Mints no claim, and returns the toast's own id.
 *
 * This is the route for a caller that will not release: it has nothing to take
 * back, so it is handed the toast's identity rather than a handle on it.
 */
export function pushToast(input: ToastInput): string {
  return useToastStore.getState().push(input);
}

/**
 * Push a toast from outside React and keep a claim on it.
 *
 * The sibling of `pushToast`, for a sticky toast (`duration: 0`) reporting an
 * action still in flight: the caller holds the claim this returns and passes it
 * to `releaseToast` when the action settles, on either branch. Only a caller
 * that will release may mint, so the claim set holds only claims somebody is
 * still holding.
 */
export function pushClaimedToast(input: ToastInput): string {
  return useToastStore.getState().pushClaimed(input);
}

/**
 * Give up this call's claim on a toast pushed from outside React.
 *
 * The sibling of `pushClaimedToast`: the caller holds the CLAIM that returned
 * and releases it when the action settles, on either branch. The toast goes
 * when the last caller relying on it releases, so an overlapping twin keeps
 * its indicator. A claim that no toast holds — stale, unknown, or a toast id —
 * is a silent no-op.
 */
export function releaseToast(claimId: string): void {
  useToastStore.getState().release(claimId);
}

/**
 * Best readable line for whatever a rejected action threw.
 *
 * `fallback` is what the caller wants shown when the throw carries no message
 * of its own ("Failed to delete branch") — it replaces the inline
 * `err instanceof Error ? err.message : "…"` ternary, which loses the ApiError
 * status line and was copy-pasted across ~45 call sites.
 */
export function describeError(error: unknown, fallback = "Unexpected error"): string {
  if (error instanceof ApiError) {
    return error.message || `Request failed (${error.status})`;
  }
  if (error instanceof Error) {
    return error.message || error.name;
  }
  if (typeof error === "string" && error) return error;
  return fallback;
}

/**
 * The HTTP status a failed request carried, or null if it was not one.
 *
 * A sibling of `describeError`: the narrowing lives here so callers asking
 * "was this a 409?" do not each write their own `instanceof ApiError` at the
 * call site. Status is what distinguishes a refusal the caller may override
 * from one it may not.
 */
export function errorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}

/** The characters that already end a sentence, so nothing is appended after one. */
const SENTENCE_TERMINATORS = new Set([".", "!", "?", "\u2026", ":", ";"]);

/**
 * `text`, made safe to place before another sentence.
 *
 * A third sibling of `describeError`: concatenating a message we do not control
 * with a sentence we do is a contract, and the server's side of it is one line
 * with no terminator. Without this, `… at line 4, column 12 Fix that file.`
 * renders as two sentences run together, and the defect hides from tests
 * whenever the *fallback* happens to end in a period.
 *
 * Only the last character decides, and only trailing whitespace is removed:
 * leading indentation and internal newlines are the structure of a multi-line
 * parse failure, which is rendered `pre-wrap`.
 */
export function endSentence(text: string): string {
  const trimmed = text.replace(/\s+$/u, "");
  if (!trimmed) return "";
  return SENTENCE_TERMINATORS.has(trimmed[trimmed.length - 1]) ? trimmed : `${trimmed}.`;
}

/**
 * Report an action that did not complete.
 *
 * `title` names the action ("Delete ticket"), not the failure — the toast
 * appends "failed" so every call reads the same way.
 */
/**
 * A degraded-but-continuing step: the action still happened, and this names what
 * could not be checked or did not run alongside it.
 */
export function toastWarning(title: string, error: unknown, fallback: string): string {
  return pushToast({ tone: "warning", title, message: describeError(error, fallback) });
}

export interface ToastActionFailedOptions {
  /**
   * ms before the toast expires. Omitted, an error toast is sticky — the one
   * caller that passes it is `queryCache.onError`, whose cold-cache load
   * failure carries nothing to copy and re-reports itself on the next poll.
   */
  duration?: number;
}

export function toastActionFailed(
  title: string,
  error: unknown,
  options?: ToastActionFailedOptions,
): string {
  return pushToast({
    tone: "error",
    title: title.endsWith("failed") ? title : `${title} failed`,
    message: describeError(error),
    duration: options?.duration,
  });
}
