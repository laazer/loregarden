import { MutationCache, QueryClient } from "@tanstack/react-query";

import { toastActionFailed } from "../state/toastStore";

/**
 * The app's query client, with one rule on top of the defaults: a mutation is a
 * user action, so a rejected one reports itself instead of dying in a console
 * line nobody is reading.
 *
 * Callers name the action with `meta.errorTitle` ("Delete ticket"); a mutation
 * that already renders its own failure inline sets `meta.suppressErrorToast`.
 * Queries are left alone — a failed read belongs in the panel that wanted it,
 * and refetch loops would toast on a timer.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.suppressErrorToast) return;
        toastActionFailed(mutation.meta?.errorTitle ?? "Action", error);
      },
    }),
  });
}
