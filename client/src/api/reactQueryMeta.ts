/**
 * Types the `meta` a mutation may carry, so the global error toast in App.tsx
 * reads it without a cast and callers get completion on it.
 */
export interface MutationErrorMeta extends Record<string, unknown> {
  /** Names the action for the failure toast, e.g. "Delete ticket". */
  errorTitle?: string;
  /** Set when the mutation renders its own failure and a toast would double it. */
  suppressErrorToast?: boolean;
}

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: MutationErrorMeta;
  }
}
