/**
 * Agent Studio's first screen: every agent, worst failure rate first, each a
 * link to its editor. It replaces a blank "New agent" form as the landing page;
 * New agent… now opens a dialog from here.
 *
 * Run counts are a second read. If only that one fails the table still lists
 * the agents (so the editor is still one click away) and says the counts are
 * missing, rather than drawing every agent as never having run.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import type { AgentRunStats, StudioAgent } from "../../api/types";
import {
  MIN_RUNS_FOR_RATE,
  agentIndexRows,
  isFailureAlert,
  matchesAgentFilter,
  ratePercent,
  sortAgentRows,
  type AgentIndexRow,
  type AgentSortKey,
  type SortDirection,
} from "../../lib/agentIndex";
import { studioAgentPath } from "../../lib/appNavigation";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import "./AgentIndex.css";

interface Column {
  key: AgentSortKey;
  label: string;
  numeric: boolean;
  /** The direction a first click sorts: worst or most first for numbers. */
  first: SortDirection;
}

const COLUMNS: Column[] = [
  { key: "name", label: "Agent", numeric: false, first: "ascending" },
  { key: "runs", label: "Runs", numeric: true, first: "descending" },
  { key: "failed", label: "Failed", numeric: true, first: "descending" },
  { key: "rate", label: "Failure rate", numeric: true, first: "descending" },
  { key: "lastRun", label: "Last run", numeric: true, first: "descending" },
];

interface Query<T> {
  data: T | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

interface Props {
  agents: Query<StudioAgent[]>;
  stats: Query<AgentRunStats[]>;
  onNewAgent: () => void;
  newAgentOpen: boolean;
}

function RateCell({ row }: { row: AgentIndexRow }) {
  if (row.rate === null) return <span className="agent-index-muted">—</span>;
  const percent = ratePercent(row.rate);
  const alert = isFailureAlert(row);
  return (
    <span
      className={`agent-index-rate${alert ? " is-alert" : ""}${row.rateReliable ? "" : " is-thin"}`}
    >
      <span className="agent-index-rate-track" aria-hidden>
        <span className="agent-index-rate-fill" style={{ transform: `scaleX(${row.rate})` }} />
      </span>
      {percent}%
    </span>
  );
}

export function AgentIndex({ agents, stats, onNewAgent, newAgentOpen }: Props) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: AgentSortKey; dir: SortDirection }>({
    key: "rate",
    dir: "descending",
  });

  if (agents.isPending) return <PaneSkeleton variant="list" label="Loading agents…" />;
  if (agents.isError || !agents.data) {
    return (
      <div className="agent-index agent-index--message" role="alert">
        <p>Could not load the agents: {describeError(agents.error, "the request failed")}</p>
        <Button variant="secondary" onClick={() => void agents.refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  const statsReady = Boolean(stats.data);
  const allRows = agentIndexRows(agents.data, stats.data ?? []);
  const rows = sortAgentRows(
    allRows.filter((row) => matchesAgentFilter(row, query)),
    sort.key,
    sort.dir,
  );
  const totalRuns = allRows.reduce((sum, row) => sum + row.runs, 0);
  const totalFailed = allRows.reduce((sum, row) => sum + row.failed, 0);
  const neverRun = statsReady ? allRows.filter((row) => !row.lastRunAt) : [];

  const onSort = (column: Column) =>
    setSort((current) =>
      current.key === column.key
        ? { key: column.key, dir: current.dir === "ascending" ? "descending" : "ascending" }
        : { key: column.key, dir: column.first },
    );

  return (
    <section className="agent-index" aria-labelledby="agent-index-title">
      <header className="agent-index-head">
        <div>
          <h2 id="agent-index-title" className="agent-index-title">
            Agents
          </h2>
          <p className="agent-index-summary">
            {allRows.length} agents
            {statsReady
              ? ` · ${totalRuns.toLocaleString()} finished runs · ${totalFailed.toLocaleString()} failed`
              : ""}
          </p>
        </div>
        <Input
          type="search"
          className="agent-index-filter"
          aria-label="Filter agents"
          placeholder="Filter agents"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Button variant="primary" onClick={onNewAgent} aria-expanded={newAgentOpen}>
          New agent…
        </Button>
      </header>

      {stats.isError ? (
        <div className="agent-index-warning" role="alert">
          <span>
            Run counts did not load ({describeError(stats.error, "the request failed")}), so the
            table cannot rank agents yet.
          </span>
          <Button variant="secondary" compact onClick={() => void stats.refetch()}>
            Try again
          </Button>
        </div>
      ) : null}

      {allRows.length === 0 ? (
        <p className="agent-index-empty">
          No agents yet. New agent… creates one; built-in agents appear here once the server has
          seeded them.
        </p>
      ) : rows.length === 0 ? (
        <p className="agent-index-empty">
          No agent matches “{query}”.{" "}
          <Button variant="plain" className="agent-index-clear" onClick={() => setQuery("")}>
            Show every agent
          </Button>
        </p>
      ) : (
        <table className="agent-index-table">
          <thead>
            <tr>
              {COLUMNS.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={column.numeric ? "num" : undefined}
                  aria-sort={sort.key === column.key ? sort.dir : "none"}
                >
                  <Button variant="plain" className="agent-index-sort" onClick={() => onSort(column)}>
                    {column.label}
                    <span aria-hidden className="agent-index-sort-mark">
                      {sort.key === column.key ? (sort.dir === "ascending" ? "↑" : "↓") : ""}
                    </span>
                  </Button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.agent.slug} className={isFailureAlert(row) ? "is-alert" : undefined}>
                <th scope="row">
                  <Link className="agent-index-name" to={studioAgentPath(row.agent.slug)}>
                    {row.agent.name}
                  </Link>
                  <span className="agent-index-slug">
                    {row.agent.slug}
                    {row.agent.built_in ? "" : " · custom"}
                  </span>
                </th>
                <td className="num" data-label="Runs">
                  {statsReady ? row.runs.toLocaleString() : "—"}
                </td>
                <td className="num" data-label="Failed">
                  {statsReady ? row.failed.toLocaleString() : "—"}
                </td>
                <td className="num" data-label="Failure rate">
                  {statsReady ? <RateCell row={row} /> : "—"}
                </td>
                <td className="num" data-label="Last run">
                  {row.lastRunAt ? (
                    formatRelativeAge(row.lastRunAt)
                  ) : (
                    <span className="agent-index-muted">{statsReady ? "never" : "—"}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {statsReady && rows.length ? (
        <p className="agent-index-foot">
          A rate from fewer than {MIN_RUNS_FOR_RATE} finished runs is faded and ranks last.
          {neverRun.length
            ? ` ${neverRun.length} ${neverRun.length === 1 ? "agent has" : "agents have"} never run: ${neverRun
                .map((row) => row.agent.slug)
                .join(", ")}.`
            : ""}
        </p>
      ) : null}
    </section>
  );
}
