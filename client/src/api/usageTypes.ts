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

// Weekly token trend across agent runs, served by GET /api/runs/usage/trend.

export type TokenTrendMetric = "total" | "uncached";
export type TokenTrendDirection = "down" | "up" | "flat" | "unknown";

export interface TokenTrendWeek {
  week_start: string;
  /** The current week, still filling up; left out of the comparison. */
  partial: boolean;
  tickets: number;
  runs: number;
  measured_runs: number;
  unmeasured_runs: number;
  /** null means no run that week reported a figure — never render it as 0. */
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  total_tokens: number | null;
  median_tokens_per_run: number | null;
}

export interface TokenTrendComparison {
  recent_start: string;
  prior_start: string;
  recent_measured_runs: number;
  prior_measured_runs: number;
  recent_median: number | null;
  prior_median: number | null;
  /** Negative is cheaper; null when either side has too few measured runs. */
  change_percent: number | null;
  direction: TokenTrendDirection;
}

export interface TokenTrend {
  weeks: TokenTrendWeek[];
  comparison: TokenTrendComparison;
  metric: TokenTrendMetric;
  models: string[];
  stages: string[];
}

export interface TokenTrendQuery {
  metric: TokenTrendMetric;
  model: string | null;
  stage_key: string | null;
}
