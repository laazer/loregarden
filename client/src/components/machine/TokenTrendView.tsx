import { useState } from "react";

import type {
  TokenTrend,
  TokenTrendComparison,
  TokenTrendMetric,
  TokenTrendQuery,
  TokenTrendWeek,
} from "../../api/usageTypes";
import { useTokenTrend } from "../../hooks/useTokenTrend";
import { formatTokens } from "../../lib/formatTokens";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { Select } from "../ui/Select";
import "./TokenTrendView.css";

const METRIC_LABELS: Record<TokenTrendMetric, string> = {
  total: "All tokens",
  uncached: "Uncached (no cache reads)",
};

const ALL = "";

function weekLabel(isoDate: string): string {
  const [, month, day] = isoDate.split("-").map(Number);
  return `${month}/${day}`;
}

function comparisonSentence(cmp: TokenTrendComparison): string {
  const since = `the 4 weeks from ${weekLabel(cmp.prior_start)}`;
  if (cmp.change_percent === null) {
    return (
      `Not enough measured runs to compare: ${cmp.prior_measured_runs} in ${since}, ` +
      `${cmp.recent_measured_runs} in the 4 weeks from ${weekLabel(cmp.recent_start)} ` +
      "(20 needed on each side)."
    );
  }
  const verb =
    cmp.direction === "down"
      ? "Down"
      : cmp.direction === "up"
        ? "Up"
        : "About flat,";
  const pct = `${Math.abs(cmp.change_percent).toFixed(1)}%`;
  return (
    `${verb} ${pct} per run: median ${formatTokens(cmp.prior_median)} over ` +
    `${cmp.prior_measured_runs} runs in ${since}, ${formatTokens(cmp.recent_median)} over ` +
    `${cmp.recent_measured_runs} runs in the 4 weeks from ${weekLabel(cmp.recent_start)}.`
  );
}

function TrendHeadline({ cmp }: { cmp: TokenTrendComparison }) {
  const tone =
    cmp.direction === "down"
      ? "good"
      : cmp.direction === "up"
        ? "bad"
        : "neutral";
  return (
    <section
      className={`token-trend-headline token-trend-headline--${tone}`}
      aria-live="polite"
    >
      <span className="token-trend-headline-mark" aria-hidden="true">
        {cmp.direction === "down" ? "↓" : cmp.direction === "up" ? "↑" : "→"}
      </span>
      <p>{comparisonSentence(cmp)}</p>
    </section>
  );
}

const CHART_HEIGHT = 140;
const BAR_GAP = 6;

/**
 * One bar per week: the median tokens per measured run. Decorative beside the
 * table below, which carries every figure for a screen reader or an agent;
 * each bar still names its value on hover.
 */
function TrendChart({ weeks }: { weeks: TokenTrendWeek[] }) {
  const peak = Math.max(1, ...weeks.map((w) => w.median_tokens_per_run ?? 0));
  const width = 100 / weeks.length;
  return (
    <figure className="token-trend-chart">
      <svg
        viewBox={`0 0 100 ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Median tokens per run, by week. The table below has the figures."
      >
        {weeks.map((week, index) => {
          const value = week.median_tokens_per_run;
          const height =
            value === null
              ? 0
              : Math.max(2, (value / peak) * (CHART_HEIGHT - 4));
          return (
            <rect
              key={week.week_start}
              className={`token-trend-bar${week.partial ? " token-trend-bar--partial" : ""}`}
              x={index * width + BAR_GAP / weeks.length / 2}
              width={width - BAR_GAP / weeks.length}
              y={CHART_HEIGHT - height}
              height={height}
              rx={0.6}
            >
              <title>
                {`Week of ${week.week_start}${week.partial ? " (so far)" : ""}: ` +
                  (value === null
                    ? "no measured runs"
                    : `median ${formatTokens(value)} over ${week.measured_runs} runs`)}
              </title>
            </rect>
          );
        })}
      </svg>
      <figcaption className="token-trend-axis" aria-hidden="true">
        {weeks.map((week) => (
          <span key={week.week_start}>{weekLabel(week.week_start)}</span>
        ))}
      </figcaption>
    </figure>
  );
}

function TrendTable({ weeks }: { weeks: TokenTrendWeek[] }) {
  const newestFirst = [...weeks].reverse();
  return (
    <table className="token-trend-table">
      <caption>
        Every week, newest first. A dash means no run that week reported a
        figure.
      </caption>
      <thead>
        <tr>
          <th scope="col">Week of</th>
          <th scope="col">Median / run</th>
          <th scope="col">Runs</th>
          <th scope="col">Unmeasured</th>
          <th scope="col">Tickets</th>
          <th scope="col">Input</th>
          <th scope="col">Output</th>
          <th scope="col">Cache read</th>
          <th scope="col">Cache write</th>
          <th scope="col">Total</th>
        </tr>
      </thead>
      <tbody>
        {newestFirst.map((week) => (
          <tr key={week.week_start}>
            <th scope="row">
              {week.week_start}
              {week.partial ? (
                <span className="token-trend-partial"> so far</span>
              ) : null}
            </th>
            <td>{formatTokens(week.median_tokens_per_run)}</td>
            <td>{week.runs}</td>
            <td>{week.unmeasured_runs}</td>
            <td>{week.tickets}</td>
            <td>{formatTokens(week.input_tokens)}</td>
            <td>{formatTokens(week.output_tokens)}</td>
            <td>{formatTokens(week.cache_read_tokens)}</td>
            <td>{formatTokens(week.cache_write_tokens)}</td>
            <td>{formatTokens(week.total_tokens)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface FiltersProps {
  query: TokenTrendQuery;
  trend: TokenTrend | undefined;
  busy: boolean;
  onChange: (next: TokenTrendQuery) => void;
}

function TrendFilters({ query, trend, busy, onChange }: FiltersProps) {
  // A chosen value stays offered even if the window no longer contains it.
  const models = Array.from(
    new Set([...(trend?.models ?? []), query.model ?? ALL]),
  ).filter(Boolean);
  const stages = Array.from(
    new Set([...(trend?.stages ?? []), query.stage_key ?? ALL]),
  ).filter(Boolean);
  return (
    <div className="token-trend-filters" aria-busy={busy}>
      <label>
        <span>Count</span>
        <Select
          aria-label="Count"
          value={query.metric}
          onChange={(e) =>
            onChange({ ...query, metric: e.target.value as TokenTrendMetric })
          }
        >
          {(Object.keys(METRIC_LABELS) as TokenTrendMetric[]).map((metric) => (
            <option key={metric} value={metric}>
              {METRIC_LABELS[metric]}
            </option>
          ))}
        </Select>
      </label>
      <label>
        <span>Model</span>
        <Select
          aria-label="Model"
          value={query.model ?? ALL}
          onChange={(e) =>
            onChange({ ...query, model: e.target.value || null })
          }
        >
          <option value={ALL}>All models</option>
          {models.map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </Select>
      </label>
      <label>
        <span>Stage</span>
        <Select
          aria-label="Stage"
          value={query.stage_key ?? ALL}
          onChange={(e) =>
            onChange({ ...query, stage_key: e.target.value || null })
          }
        >
          <option value={ALL}>All stages</option>
          {stages.map((stage) => (
            <option key={stage} value={stage}>
              {stage}
            </option>
          ))}
        </Select>
      </label>
    </div>
  );
}

/**
 * Machine → Tokens: "are agent runs getting cheaper in tokens?"
 *
 * Answers with the median tokens per measured run, last 4 complete weeks
 * against the 4 before, because weekly totals rise with run volume and would
 * say nothing about cost per run. The filters are the next action: a trend
 * over mixed models compares different populations, so the view says so until
 * one is chosen.
 */
export function TokenTrendView() {
  const [query, setQuery] = useState<TokenTrendQuery>({
    metric: "total",
    model: null,
    stage_key: null,
  });
  const trend = useTokenTrend(query);
  const data = trend.data;

  let body;
  if (trend.isPending) {
    body = (
      <PaneSkeleton variant="list" rows={6} label="Loading token trend…" />
    );
  } else if (trend.isError && !data) {
    body = (
      <div className="token-trend-message" role="alert">
        <p>{describeError(trend.error, "The token trend did not load.")}</p>
        <Button
          variant="secondary"
          compact
          onClick={() => void trend.refetch()}
        >
          Retry
        </Button>
      </div>
    );
  } else if (!data || data.weeks.every((week) => week.runs === 0)) {
    body = (
      <div className="token-trend-message">
        <p>
          No finished agent runs match in the last {data?.weeks.length ?? 12}{" "}
          weeks. A run records its tokens when it finishes; clear a filter, or
          start a ticket to see one here.
        </p>
      </div>
    );
  } else {
    const mixed = query.model === null && data.models.length > 1;
    body = (
      <>
        <TrendHeadline cmp={data.comparison} />
        {mixed ? (
          <p className="token-trend-note">
            These weeks mix {data.models.length} models. Choose one under Model
            to compare like with like.
          </p>
        ) : null}
        <TrendChart weeks={data.weeks} />
        <TrendTable weeks={data.weeks} />
      </>
    );
  }

  return (
    <div className="token-trend-view">
      <TrendFilters
        query={query}
        trend={data}
        busy={trend.isFetching}
        onChange={setQuery}
      />
      {body}
    </div>
  );
}
