// Provider usage shapes, served by GET /api/usage.
// Split out of types.ts, which is over the module size limit.

export type UsageMeterStatus = "ok" | "warning" | "critical";

export interface UsageMeter {
  key: string;
  label: string;
  used: number;
  limit: number | null;
  unit: "percent" | "dollars" | string;
  percent_used: number | null;
  resets_at: string | null;
  status: UsageMeterStatus;
}

export interface UsageBreakdownItem {
  name: string;
  amount: number;
  unit: string;
  share_percent: number;
}

export interface UsageProviderSnapshot {
  provider: "claude" | "cursor" | "codex";
  plan: string | null;
  logged_in: boolean;
  error: string | null;
  meters: UsageMeter[];
  breakdown: UsageBreakdownItem[];
  from_cache: boolean;
  cached_at: string | null;
  /** Model this provider's adapter is pinned to, or null for the CLI default. */
  configured_model: string | null;
  /** Whether a run started now would use this provider's adapter. */
  active_adapter: boolean;
  /**
   * When the reading itself was taken, for providers that report their own
   * freshness rather than being fetched live. Distinct from `cached_at`, which
   * means a live fetch failed and this is the last good reading.
   */
  observed_at: string | null;
}

export interface UsageSnapshot {
  providers: UsageProviderSnapshot[];
  near_limit: boolean;
  warnings: string[];
  fetched_at: string;
}
