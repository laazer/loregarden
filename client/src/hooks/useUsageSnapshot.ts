import { useQuery } from "@tanstack/react-query";
import { useCallback, useRef } from "react";

import { api } from "../api/client";

const USAGE_REFRESH_MS = 30 * 60_000;

/**
 * Claude and Cursor usage, for the Machine page's Usage dialog and the rail's
 * near-limit warning. One query key, so both read the same snapshot.
 *
 * The snapshot is served from a short server-side cache so a page load never
 * blocks on the providers; `refresh` asks for live numbers, which is what the
 * dialog's Refresh button means.
 */
export function useUsageSnapshot() {
  const forceRefresh = useRef(false);
  const query = useQuery({
    queryKey: ["usage"],
    queryFn: () => {
      const force = forceRefresh.current;
      forceRefresh.current = false;
      return api.usage(force);
    },
    refetchInterval: USAGE_REFRESH_MS,
    staleTime: USAGE_REFRESH_MS,
    refetchOnWindowFocus: false,
    meta: { errorTitle: "Load usage" },
  });
  const refresh = useCallback(() => {
    forceRefresh.current = true;
    void query.refetch();
  }, [query]);
  return { query, refresh };
}
