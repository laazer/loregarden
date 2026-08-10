import { create } from "zustand";

import { ApiError } from "../api/client";

export type ToastTone = "error" | "warning" | "success" | "info";

export interface Toast {
  id: string;
  tone: ToastTone;
  title: string;
  message: string;
  /** ms on screen before auto-dismiss; 0 keeps it until dismissed. */
  duration: number;
}

export interface ToastInput {
  tone?: ToastTone;
  title: string;
  message?: string;
  duration?: number;
}

/** Errors stay up longer — they carry a message the operator has to read. */
const DEFAULT_DURATION: Record<ToastTone, number> = {
  error: 9000,
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

interface ToastState {
  toasts: Toast[];
  push: (input: ToastInput) => string;
  dismiss: (id: string) => void;
  clear: () => void;
}

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (input) => {
    const tone = input.tone ?? "error";
    const toast: Toast = {
      id: `toast-${++seq}`,
      tone,
      title: input.title,
      message: input.message ?? "",
      duration: input.duration ?? DEFAULT_DURATION[tone],
    };
    set((state) => {
      // A repeated failure replaces its twin rather than stacking: same text,
      // fresh timer, so the operator sees it is still happening without the
      // stack filling up with copies.
      const deduped = state.toasts.filter(
        (existing) =>
          existing.tone !== toast.tone ||
          existing.title !== toast.title ||
          existing.message !== toast.message,
      );
      return { toasts: [...deduped, toast].slice(-MAX_TOASTS) };
    });
    return toast.id;
  },
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
  clear: () => set({ toasts: [] }),
}));

/** Push a toast from outside React (mutation cache, plain catch blocks). */
export function pushToast(input: ToastInput): string {
  return useToastStore.getState().push(input);
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
 * Report an action that did not complete.
 *
 * `title` names the action ("Delete ticket"), not the failure — the toast
 * appends "failed" so every call reads the same way.
 */
export function toastActionFailed(title: string, error: unknown): string {
  return pushToast({
    tone: "error",
    title: title.endsWith("failed") ? title : `${title} failed`,
    message: describeError(error),
  });
}
