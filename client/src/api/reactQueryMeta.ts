/**
 * Types the `meta` a mutation or a query may carry, so the global error toasts
 * in `createQueryClient` read it without a cast and callers get completion on it.
 */
export interface MutationErrorMeta extends Record<string, unknown> {
  /** Names the action for the failure toast, e.g. "Delete ticket". */
  errorTitle?: string;
  /** Set when the mutation renders its own failure and a toast would double it. */
  suppressErrorToast?: boolean;
}

/**
 * The query-side twin. Same two keys, same meaning: name the read for the toast
 * ("Load tickets"), or opt out when the panel already renders the failure.
 */
export interface QueryErrorMeta extends Record<string, unknown> {
  /** Names the read for the failure toast, e.g. "Load tickets". */
  errorTitle?: string;
  /** Set when the panel renders its own failure and a toast would double it. */
  suppressErrorToast?: boolean;
}

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: MutationErrorMeta;
    queryMeta: QueryErrorMeta;
  }
}
