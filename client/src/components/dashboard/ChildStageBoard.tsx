import { useQuery } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";

import { api } from "../../api/client";
import type { TicketSummary, TicketTreeNode, WorkflowStageView } from "../../api/types";
import {
  BOARD_GROUPS,
  type BoardColumn,
  type BoardGroup,
  boardColumns,
  boardGroup,
  cellLabel,
  cellMark,
  isActive,
  stageCounts,
} from "../../lib/childStageBoard";
import { partStanding, partsProgress } from "../../lib/ticketParts";
import { describeError } from "../../state/toastStore";
import { LaneLogView } from "../logs/LaneLogView";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { PaneSkeleton } from "../ui/PaneSkeleton";

import "./ChildStageBoard.css";

/** Past this many rows a group starts folded behind its count. */
const FOLD_AT = 12;
/** Past this many children the board gets a filter over title and id. */
const FILTER_AT = 50;

/**
 * "Where is each child in its pipeline, and where is work stuck?"
 *
 * One row per child, one column per workflow stage. The counts above the
 * columns say how many children sit at each stage now. A running row opens on
 * its current run's live log.
 */
export function ChildStageBoard({
  parentId,
  parentStages,
  childNodes,
  onOpenTicket,
  onOpenRunLog,
}: {
  parentId: string;
  /** The parent's own workflow: the columns when no child has started one yet. */
  parentStages: readonly WorkflowStageView[];
  /** The board's tree under the parent, for a child's own parts. */
  childNodes: readonly TicketTreeNode[];
  onOpenTicket: (ticketId: string) => void;
  onOpenRunLog: (runId: string) => void;
}) {
  const children = useQuery({
    queryKey: ["child-stage-board", parentId],
    queryFn: () => api.tickets({ parent_ticket_id: parentId }),
    refetchInterval: (query) => ((query.state.data ?? []).some(isActive) ? 3000 : 15000),
  });
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [unfolded, setUnfolded] = useState<ReadonlySet<BoardGroup>>(new Set());
  const [filter, setFilter] = useState("");

  const rows = children.data ?? [];
  const columns = useMemo(() => boardColumns(rows, parentStages), [rows, parentStages]);
  const counts = useMemo(() => stageCounts(rows, columns), [rows, columns]);
  const nodes = useMemo(() => new Map(childNodes.map((node) => [node.id, node])), [childNodes]);

  if (children.isPending) return <PaneSkeleton variant="list" rows={6} label="Loading the tickets under this one…" />;
  if (children.isError) {
    return (
      <div className="ap-state" role="alert">
        <div className="ap-state-title">Couldn&rsquo;t load the tickets under this one</div>
        <div className="ap-state-body">{describeError(children.error, "The request failed.")}</div>
        <Button variant="secondary" compact onClick={() => void children.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="ap-state">
        <div className="ap-state-title">No tickets under this one yet</div>
        <div className="ap-state-body">Add a sub-item from the header. Each one shows here with the stage it has reached.</div>
      </div>
    );
  }

  const needle = filter.trim().toLowerCase();
  const shown = needle ? rows.filter((row) => `${row.title} ${row.external_id}`.toLowerCase().includes(needle)) : rows;
  const done = rows.filter((row) => row.state === "done").length;
  const running = rows.filter((row) => boardGroup(row) === "running").length;
  const blocked = rows.filter((row) => row.state === "blocked").length;
  const grid = { gridTemplateColumns: `minmax(200px, 240px) repeat(${columns.length}, minmax(44px, 1fr))` };
  const toggle = <T,>(set: ReadonlySet<T>, value: T): ReadonlySet<T> => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  };

  return (
    <section className="csb" aria-label="Where each child is">
      <div className="csb-head">
        <span>
          <b>
            {done} of {rows.length}
          </b>{" "}
          done
          {running ? (
            <>
              {" · "}
              <b>{running}</b> running
            </>
          ) : null}
          {blocked ? (
            <>
              {" · "}
              <b className="csb-bad">{blocked}</b> blocked
            </>
          ) : null}
        </span>
        {rows.length > FILTER_AT ? (
          <Input
            type="search"
            className="csb-filter"
            aria-label="Filter the tickets under this one by title or id"
            placeholder={`Filter ${rows.length} tickets…`}
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        ) : null}
      </div>

      <div className="csb-scroll">
        <div className="csb-grid" style={grid} role="table" aria-label="Stages of each child">
          <div className="csb-counts" role="row">
            <span role="cell" />
            {columns.map((column) => {
              const n = counts.get(column.key) ?? 0;
              return (
                <span key={column.key} className="csb-count" role="cell" aria-label={`${n} at ${column.name}`}>
                  {n ? <span className="csb-count-n">{n}</span> : null}
                  {n ? <span className="csb-count-bar" style={{ height: `${Math.min(40, 4 + n * 4)}px` }} /> : null}
                </span>
              );
            })}
          </div>
          <div className="csb-row" role="row">
            <span className="csb-hd csb-hd--name" role="columnheader">
              Ticket
            </span>
            {columns.map((column) => (
              <span key={column.key} className="csb-hd" role="columnheader" title={column.name}>
                {column.name}
              </span>
            ))}
          </div>

          {BOARD_GROUPS.map(({ key, label }) => {
            const members = shown.filter((row) => boardGroup(row) === key);
            if (members.length === 0) return null;
            const folded = !needle && members.length > FOLD_AT && !unfolded.has(key);
            return (
              <Fragment key={key}>
                <div className="csb-group" role="row">
                  <span role="cell">
                    {label} <span className="csb-group-n">{members.length}</span>
                  </span>
                  {members.length > FOLD_AT && !needle ? (
                    <Button
                      variant="plain"
                      className="csb-fold"
                      aria-expanded={!folded}
                      onClick={() => setUnfolded((was) => toggle(was, key))}
                    >
                      {folded ? `Show ${members.length}` : "Hide"}
                    </Button>
                  ) : null}
                </div>
                {folded
                  ? null
                  : members.map((child) => (
                      <BoardRow
                        key={child.id}
                        child={child}
                        node={nodes.get(child.id)}
                        columns={columns}
                        settled={key === "done" || key === "wont_do"}
                        expanded={open.has(child.id)}
                        onToggle={() => setOpen((was) => toggle(was, child.id))}
                        onOpenTicket={onOpenTicket}
                        onOpenRunLog={onOpenRunLog}
                      />
                    ))}
              </Fragment>
            );
          })}
        </div>
      </div>
      {needle && shown.length === 0 ? (
        <p className="csb-none">No ticket under this one has &ldquo;{filter.trim()}&rdquo; in its title or id.</p>
      ) : null}
    </section>
  );
}

function BoardRow({
  child,
  node,
  columns,
  settled,
  expanded,
  onToggle,
  onOpenTicket,
  onOpenRunLog,
}: {
  child: TicketSummary;
  node: TicketTreeNode | undefined;
  columns: readonly BoardColumn[];
  settled: boolean;
  expanded: boolean;
  onToggle: () => void;
  onOpenTicket: (ticketId: string) => void;
  onOpenRunLog: (runId: string) => void;
}) {
  const active = isActive(child);
  const parts = child.child_count > 0 ? (node?.children ?? []) : [];
  return (
    <>
      <div className={`csb-row${settled ? " csb-row--settled" : ""}`} role="row">
        <span className="csb-name" role="rowheader">
          {active ? (
            <Button
              variant="plain"
              className="csb-expand"
              aria-expanded={expanded}
              aria-label={expanded ? `Hide the live log of ${child.external_id}` : `Show the live log of ${child.external_id}`}
              onClick={onToggle}
            >
              {expanded ? "▾" : "▸"}
            </Button>
          ) : null}
          <span className="csb-name-text">
            <Button variant="plain" className="csb-title" onClick={() => onOpenTicket(child.id)}>
              {child.title}
            </Button>
            <span className="csb-ref">
              {child.external_id}
              {child.state !== "done" && child.workflow_stage_name ? (
                <span className={child.state === "blocked" ? "csb-ref-bad" : active ? "csb-ref-run" : undefined}>
                  {" · "}
                  {child.workflow_stage_name}
                  {child.state === "blocked" ? " blocked" : active ? " running" : ""}
                </span>
              ) : null}
            </span>
          </span>
        </span>
        {parts.length > 0 ? (
          <PartsCell parts={parts} span={columns.length} />
        ) : (
          columns.map((column, index) => {
            const mark = cellMark(child, column.key);
            const reached = mark === "done" || mark === "wont_do";
            const isLast = index === columns.length - 1;
            return (
              <span
                key={column.key}
                className={`csb-cell${reached ? " csb-cell--past" : ""}${index === 0 ? " csb-cell--first" : ""}${isLast ? " csb-cell--last" : ""}`}
                role="cell"
                aria-label={cellLabel(column, mark)}
                title={cellLabel(column, mark)}
              >
                <span className={`csb-mark csb-mark--${isLast && mark === "done" ? "landed" : mark}`} aria-hidden />
              </span>
            );
          })
        )}
      </div>
      {active && expanded ? <ChildLiveLog ticketId={child.id} onOpenRunLog={onOpenRunLog} /> : null}
    </>
  );
}

function PartsCell({ parts, span }: { parts: readonly TicketTreeNode[]; span: number }) {
  const progress = partsProgress(parts);
  const running = parts.filter((part) => partStanding(part) === "running").length;
  return (
    <span className="csb-parts" role="cell" style={{ gridColumn: `span ${span}` }}>
      <span className="csb-parts-bar" aria-hidden>
        {parts.map((part) => (
          <i key={part.id} className={`is-${partStanding(part)}`} />
        ))}
      </span>
      <span className="csb-parts-label">
        {progress.done} of {progress.total} under it done{running ? ` · ${running} running` : ""}
      </span>
    </span>
  );
}

/** The child's current run, on the same live feed the lane view uses. */
function ChildLiveLog({ ticketId, onOpenRunLog }: { ticketId: string; onOpenRunLog: (runId: string) => void }) {
  const runs = useQuery({
    queryKey: ["child-latest-run", ticketId],
    queryFn: () => api.runs(ticketId, 1),
    refetchInterval: 5000,
  });
  const run = runs.data?.[0];
  return (
    <div className="csb-log" role="row">
      <div className="csb-log-inner" role="cell">
        {runs.isPending ? (
          <div className="csb-log-note">Finding its current run…</div>
        ) : runs.isError ? (
          <div className="csb-log-note" role="alert">
            Couldn&rsquo;t find its current run: {describeError(runs.error, "The request failed.")}{" "}
            <Button variant="plain" className="csb-link" onClick={() => void runs.refetch()}>
              Retry
            </Button>
          </div>
        ) : !run ? (
          <div className="csb-log-note">No run has started on this ticket yet.</div>
        ) : (
          <>
            <div className="csb-log-head">
              <b>{run.stage_key}</b>
              <span>
                {run.agent_id} · {run.run_code} · {run.status}
              </span>
              <span className="csb-log-sp" />
              <Button variant="plain" className="csb-link" onClick={() => onOpenRunLog(run.id)}>
                Open full log
              </Button>
            </div>
            <div className="csb-log-body">
              <LaneLogView runId={run.id} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
