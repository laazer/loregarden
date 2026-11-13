import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import type { QueryKey } from "@tanstack/react-query";

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
  const client = new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.suppressErrorToast) return;
        toastActionFailed(mutation.meta?.errorTitle ?? "Action", error);
      },
    }),
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (query.meta?.suppressErrorToast) return;
        // The other half of this policy is in `hooks/usePolledReadFailure.ts`.
        if (query.state.data !== undefined) return;
        // The one caller that bounds an error toast. A cold-cache load failure
        // carries no value for the operator to copy and re-reports itself on
        // the next poll; left sticky, an outage would park four permanent
        // toasts in the corner of every screen. A mutation failure — something
        // a person pressed — stays sticky.
        toastActionFailed(query.meta?.errorTitle ?? "Load", error, { duration: 9000 });
      },
    }),
  });
  for (const [queryKey, staleTime] of NEAR_STATIC_QUERIES) {
    client.setQueryDefaults(queryKey, { staleTime });
  }
  return client;
}

/**
 * Data that changes when a person edits it, not on its own, so it need not be
 * refetched on every mount and window focus. The default `staleTime` of 0 made
 * each of these a request per navigation from every component that reads it
 * (`["workspaces"]` alone has two dozen). An edit still shows at once: each key's
 * mutations invalidate it, and invalidation ignores `staleTime`.
 *
 * `["workspaces"]` carries ticket counts that move without an edit here, so it
 * gets a minute rather than five. `["runtime-options"]` is absent on purpose:
 * `useRuntimeOptionsQuery` owns its refetch policy.
 */
export const NEAR_STATIC_QUERIES: ReadonlyArray<readonly [QueryKey, number]> = [
  [["workspaces"], 60_000],
  [["workflow-templates"], 5 * 60_000],
  [["studio-agents"], 5 * 60_000],
  [["studio-workflows"], 5 * 60_000],
];
