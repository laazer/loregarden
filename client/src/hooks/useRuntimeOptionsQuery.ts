import { useQuery } from "@tanstack/react-query";
import type { QueryKey, UseQueryResult } from "@tanstack/react-query";

import { api } from "../api/client";
import type { RuntimeOptions } from "../api/client";

/**
 * 1500ms against a 7.42s measured cold `opencode models` probe is roughly five
 * polls. The number is tunable with evidence; it living in one place is not.
 */
export const RUNTIME_OPTIONS_PROBING_REFETCH_MS = 1500;

type RuntimeOptionsParams = Parameters<typeof api.runtimeOptions>[0];

interface UseRuntimeOptionsQueryArgs {
  /** Kept per call site, so two sites asking different questions keep two caches. */
  queryKey: QueryKey;
  params?: RuntimeOptionsParams;
  enabled?: boolean;
}

/**
 * The one place that decides when runtime-options is re-fetched.
 *
 * Discovery answers before it has an answer: a provider reads `"probing"` while
 * its probe runs on a background thread server-side, and the catalogue only
 * arrives on a later response. Something has to ask again — and with seven call
 * sites, a policy copied into each of them is seven policies.
 *
 * Every other status is terminal from the client's side: `stale` and
 * `unavailable` are re-probed on the server's own failure TTL, so polling them
 * would turn a machine with no LM Studio into a request every 1.5s for as long
 * as a tab is open, which is this ticket's own defect wearing the opposite hat.
 * A missing block, an empty one, and a status this client has never heard of are
 * all treated the same way: settled.
 */
export function useRuntimeOptionsQuery({
  queryKey,
  params,
  enabled,
}: UseRuntimeOptionsQueryArgs): UseQueryResult<RuntimeOptions> {
  return useQuery({
    queryKey,
    queryFn: () => api.runtimeOptions(params),
    ...(enabled === undefined ? {} : { enabled }),
    refetchInterval: (query) =>
      Object.values(query.state.data?.model_discovery ?? {}).some(
        (entry) => entry?.status === "probing",
      )
        ? RUNTIME_OPTIONS_PROBING_REFETCH_MS
        : false,
  });
}
