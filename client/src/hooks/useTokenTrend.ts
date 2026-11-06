import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TokenTrendQuery } from "../api/usageTypes";

/** Runs finish every few minutes; a weekly trend does not need fresher. */
const TOKEN_TREND_STALE_MS = 5 * 60_000;

/**
 * The weekly token trend for Machine → Tokens. Keeps the previous answer on
 * screen while a filter change loads, so the chart does not blank between them.
 */
export function useTokenTrend(query: TokenTrendQuery) {
  return useQuery({
    queryKey: ["token-trend", query.metric, query.model, query.stage_key],
    queryFn: () => api.tokenTrend(query),
    staleTime: TOKEN_TREND_STALE_MS,
    placeholderData: keepPreviousData,
    refetchOnWindowFocus: false,
    meta: { errorTitle: "Load token trend" },
  });
}
