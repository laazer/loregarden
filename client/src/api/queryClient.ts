import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { toastActionFailed } from "../state/toastStore";

/**
 * The app's query client, with one rule on top of the defaults: a failure a
 * human is waiting on reports itself instead of dying in a console line nobody
 * is reading.
 *
 * Callers name the action with `meta.errorTitle` ("Delete ticket"); a mutation
 * or panel that already renders its own failure sets `meta.suppressErrorToast`.
 *
 * Mutations always toast — a user pressed something and it did not happen.
 * Queries toast only when there is no cached data to fall back on: a failed
 * background refetch over good data still shows the operator real numbers, and
 * toasting it would fire on a timer. The toast store dedupes identical toasts,
 * so a retrying query reports once rather than once per attempt.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.suppressErrorToast) return;
        toastActionFailed(mutation.meta?.errorTitle ?? "Action", error);
      },
    }),
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (query.meta?.suppressErrorToast) return;
        if (query.state.data !== undefined) return;
        toastActionFailed(query.meta?.errorTitle ?? "Load", error);
      },
    }),
  });
}
