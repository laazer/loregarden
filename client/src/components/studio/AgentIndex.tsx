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
} from "../../lib/agentIndex";
import { studioAgentPath } from "../../lib/appNavigation";
import { formatRelativeAge } from "../../lib/timestamps";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import {
  IndexLoadError,
  IndexStatsWarning,
  NoMatch,
  SortableHead,
  StudioIndexFrame,
  type IndexQuery,
} from "./StudioIndex";
import { useIndexSort, type IndexColumn } from "../../lib/indexSort";
import "./AgentIndex.css";

const COLUMNS: IndexColumn<AgentSortKey>[] = [
  { key: "name", label: "Agent", numeric: false, first: "ascending" },
  { key: "runs", label: "Runs", numeric: true, first: "descending" },
  { key: "failed", label: "Failed", numeric: true, first: "descending" },
  { key: "rate", label: "Failure rate", numeric: true, first: "descending" },
  { key: "lastRun", label: "Last run", numeric: true, first: "descending" },
];

interface Props {
  agents: IndexQuery<StudioAgent[]>;
  stats: IndexQuery<AgentRunStats[]>;
  onNewAgent: () => void;
  newAgentOpen: boolean;
}

function RateCell({ row }: { row: AgentIndexRow }) {
  if (row.rate === null) return <span className="studio-index-muted">—</span>;
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
  const { sort, onSort } = useIndexSort<AgentSortKey>({ key: "rate", dir: "descending" });

  if (agents.isPending) return <PaneSkeleton variant="list" label="Loading agents…" />;
  if (agents.isError || !agents.data) return <IndexLoadError what="agents" query={agents} />;

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

  return (
    <StudioIndexFrame
      id="agent-index-title"
      title="Agents"
      summary={`${allRows.length} agents${
        statsReady
          ? ` · ${totalRuns.toLocaleString()} finished runs · ${totalFailed.toLocaleString()} failed`
          : ""
      }`}
      noun="agent"
      query={query}
      onQuery={setQuery}
      newLabel="New agent…"
      onNew={onNewAgent}
      newOpen={newAgentOpen}
    >
      <IndexStatsWarning query={stats}>
        Run counts did not load, so the table cannot rank agents yet
      </IndexStatsWarning>

      {allRows.length === 0 ? (
        <p className="studio-index-empty">
          No agents yet. New agent… creates one; built-in agents appear here once the server has
          seeded them.
        </p>
      ) : rows.length === 0 ? (
        <NoMatch noun="agent" query={query} onClear={() => setQuery("")} />
      ) : (
        <table className="studio-index-table">
          <SortableHead columns={COLUMNS} sort={sort} onSort={onSort} />
          <tbody>
            {rows.map((row) => (
              <tr key={row.agent.slug} className={isFailureAlert(row) ? "is-alert" : undefined}>
                <th scope="row">
                  <Link className="studio-index-name" to={studioAgentPath(row.agent.slug)}>
                    {row.agent.name}
                  </Link>
                  <span className="studio-index-slug">
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
                    <span className="studio-index-muted">{statsReady ? "never" : "—"}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {statsReady && rows.length ? (
        <p className="studio-index-foot">
          A rate from fewer than {MIN_RUNS_FOR_RATE} finished runs is faded and ranks last.
          {neverRun.length
            ? ` ${neverRun.length} ${neverRun.length === 1 ? "agent has" : "agents have"} never run: ${neverRun
                .map((row) => row.agent.slug)
                .join(", ")}.`
            : ""}
        </p>
      ) : null}
    </StudioIndexFrame>
  );
}
