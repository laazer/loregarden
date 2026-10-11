import { useQuery } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";

import { api } from "../../api/client";
import type { ChildTimelineRow, ChildrenTimeline, TicketDetail } from "../../api/client";
import {
  axisTicks,
  FILTER_AT,
  groupLanes,
  idleTail,
  isBlocked,
  lastActivity,
  matchesFilter,
  needsPerson,
  nowLabel,
  nowTone,
  rowSummary,
  sectionChildren,
  segmentSpan,
  segmentTone,
  timelineAxis,
  type Axis,
  type ChildFilter,
  type Span,
} from "../../lib/parentTimeline";
import { formatRelativeAge } from "../../lib/timestamps";
import { navigateToTicket } from "../../lib/useAppNavigation";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import "./ArtifactPane.css";
import "./ParentTimeline.css";

const POLL_MS = 5000;
const COLLAPSE_LIST_AT = 12;

function useChildrenTimeline(ticketId: string, isActive: boolean) {
  return useQuery({
    queryKey: ["ticket-children-timeline", ticketId],
    queryFn: () => api.ticketChildrenTimeline(ticketId),
    refetchInterval: (query) => (isActive || hasOpenWork(query.state.data) ? POLL_MS : false),
  });
}

function hasOpenWork(timeline: ChildrenTimeline | undefined): boolean {
  return Boolean(timeline?.children.some((row) => row.segments.some((s) => s.finished_at === null)));
}

/**
 * "Where is this body of work, and what needs me?"
 *
 * A parent ticket's history is its children's: most parents never run a
 * workflow of their own. So each direct child is a row, its bar split into the
 * stage visits it made, on one time axis shared by every row. Children that
 * have not started are listed apart, as ready or waiting on named tickets.
 * A child with children of its own expands in place.
 *
 * `own` is the parent's own single-ticket timeline, offered behind a switch for
 * the few parents that ran a workflow themselves.
 */
export function ParentTimeline({
  ticket,
  isActive,
  own,
}: {
  ticket: TicketDetail;
  isActive: boolean;
  own: ReactNode;
}) {
  const [scope, setScope] = useState<"children" | "own">("children");
  return (
    <div className="tl ptl">
      <div className="ptl-scope" role="group" aria-label="Timeline scope">
        <Button
          variant="plain"
          compact
          className={`ptl-chip ${scope === "children" ? "active" : ""}`}
          aria-pressed={scope === "children"}
          onClick={() => setScope("children")}
        >
          Child tickets ({ticket.child_count})
        </Button>
        <Button
          variant="plain"
          compact
          className={`ptl-chip ${scope === "own" ? "active" : ""}`}
          aria-pressed={scope === "own"}
          onClick={() => setScope("own")}
        >
          This ticket&rsquo;s own runs
        </Button>
      </div>
      {scope === "own" ? own : <ChildrenView ticketId={ticket.id} isActive={isActive} />}
    </div>
  );
}

function ChildrenView({ ticketId, isActive }: { ticketId: string; isActive: boolean }) {
  const query = useChildrenTimeline(ticketId, isActive);
  const [filter, setFilter] = useState<ChildFilter>("all");
  const [text, setText] = useState("");
  const filterId = useId();
  // Read once per render: every row lays out against the same instant.
  const now = Date.now();

  if (query.isPending) return <PaneSkeleton variant="list" rows={8} label="Loading child tickets…" />;
  if (query.isError) {
    return (
      <div className="ap-state" role="alert">
        <div className="ap-state-title">Could not load this ticket&rsquo;s child timeline</div>
        <div className="ap-state-body">{describeError(query.error, "The request failed.")}</div>
        <Button variant="secondary" compact onClick={() => void query.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  if (query.data.children.length === 0) {
    return (
      <div className="ap-state">
        <div className="ap-state-title">No child tickets yet</div>
        <div className="ap-state-body">
          Add one from the workflow pane. Each child appears here as a row, with the stages it has run through.
        </div>
      </div>
    );
  }

  const data = query.data;
  const axis = timelineAxis(data, now);
  const all = data.children;
  const needsYou = all.filter(needsPerson);
  const blocked = all.filter(isBlocked);
  const visible = all.filter((row) => matchesFilter(row, filter, text));
  const sections = sectionChildren(visible);

  return (
    <>
      <Summary rows={all} />
      <Attention needsYou={needsYou} blocked={blocked} filter={filter} onFilter={setFilter} />

      <div className="ptl-filters">
        <div className="ptl-scope" role="group" aria-label="Show children">
          {(
            [
              ["all", `All ${all.length}`],
              ["needs_you", `Needs you ${needsYou.length}`],
              ["blocked", `Blocked ${blocked.length}`],
            ] as const
          ).map(([value, label]) => (
            <Button
              key={value}
              variant="plain"
              compact
              className={`ptl-chip ${filter === value ? "active" : ""}`}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
            >
              {label}
            </Button>
          ))}
        </div>
        {all.length > FILTER_AT ? (
          <>
            <label htmlFor={filterId} className="ptl-sr">
              Filter child tickets
            </label>
            <Input
              id={filterId}
              className="ptl-search"
              type="search"
              placeholder="Filter by id or title"
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </>
        ) : null}
      </div>

      {visible.length === 0 ? (
        <div className="ap-state">
          <div className="ap-state-title">No child tickets match</div>
          <Button
            variant="secondary"
            compact
            onClick={() => {
              setFilter("all");
              setText("");
            }}
          >
            Show all
          </Button>
        </div>
      ) : null}

      {sections.lanes.length > 0 ? (
        <section aria-label="Child tickets that have run">
          {axis ? <AxisHeader axis={axis} /> : null}
          {/* No axis means nothing below has run: the rows are parents with empty bars, there to expand. */}
          <Lanes rows={sections.lanes} axis={axis ?? { start: now - 86_400_000, end: now }} now={now} depth={0} />
        </section>
      ) : null}

      <NotStarted ready={sections.ready} waiting={sections.waiting} closed={sections.closedWithoutRuns} />
    </>
  );
}

function Summary({ rows }: { rows: readonly ChildTimelineRow[] }) {
  const count = (pred: (row: ChildTimelineRow) => boolean) => rows.filter(pred).length;
  const done = count((r) => r.state === "done" || r.state === "wont_do");
  const bad = count(isBlocked);
  const moving = count((r) => r.state === "in_progress" && !isBlocked(r));
  const rest = rows.length - done - bad - moving;
  const runs = rows.reduce((sum, r) => sum + r.total_runs, 0);
  const pct = (n: number) => `${(n / rows.length) * 100}%`;
  return (
    <div className="ptl-summary">
      <div className="tl-summary">
        <span className="tl-summary-now">
          {done} of {rows.length} done
        </span>
        <span>
          {moving} in progress · {bad} blocked · {rest} not started
        </span>
        <span>
          {runs} run{runs === 1 ? "" : "s"}
        </span>
      </div>
      <div className="ptl-progress" aria-hidden="true">
        <span className="ptl-progress-done" style={{ width: pct(done) }} />
        <span className="ptl-progress-live" style={{ width: pct(moving) }} />
        <span className="ptl-progress-bad" style={{ width: pct(bad) }} />
      </div>
    </div>
  );
}

function Attention({
  needsYou,
  blocked,
  filter,
  onFilter,
}: {
  needsYou: readonly ChildTimelineRow[];
  blocked: readonly ChildTimelineRow[];
  filter: ChildFilter;
  onFilter: (filter: ChildFilter) => void;
}) {
  if (needsYou.length === 0 && blocked.length === 0) return null;
  return (
    <div className="tl-attention">
      {needsYou.length > 0 ? (
        <section className="tl-callout tl-callout--warn" aria-label="Children waiting on you">
          <div className="tl-callout-head">
            <span className="tl-callout-title">
              {needsYou.length} child ticket{needsYou.length === 1 ? "" : "s"} waiting on you
            </span>
            <span className="tl-callout-meta">{stageCounts(needsYou)}</span>
            <Button
              variant="secondary"
              compact
              aria-pressed={filter === "needs_you"}
              onClick={() => onFilter(filter === "needs_you" ? "all" : "needs_you")}
            >
              {filter === "needs_you" ? "Show all" : `Show ${needsYou.length}`}
            </Button>
          </div>
        </section>
      ) : null}
      {blocked.length > 0 ? (
        <section className="tl-callout tl-callout--bad" aria-label="Blocked children">
          <div className="tl-callout-head">
            <span className="tl-callout-title">{blocked.length} blocked</span>
            <span className="tl-callout-meta">{stageCounts(blocked)}</span>
            <Button
              variant="secondary"
              compact
              aria-pressed={filter === "blocked"}
              onClick={() => onFilter(filter === "blocked" ? "all" : "blocked")}
            >
              {filter === "blocked" ? "Show all" : `Show ${blocked.length}`}
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  );
}

/** "11 at Playtest · 2 at Spec", most first. */
function stageCounts(rows: readonly ChildTimelineRow[]): string {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const stage = row.workflow_stage_name || row.workflow_stage_key || "no stage";
    counts.set(stage, (counts.get(stage) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([stage, n]) => `${n} at ${stage}`)
    .join(" · ");
}

const TICK_FORMAT = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const TIME_FORMAT = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

function AxisHeader({ axis }: { axis: Axis }) {
  const shortSpan = axis.end - axis.start < 2 * 86_400_000;
  const format = shortSpan ? TIME_FORMAT : TICK_FORMAT;
  return (
    <div className="ptl-row ptl-axis" aria-hidden="true">
      <span />
      <span className="ptl-axis-ticks">
        {axisTicks(axis).map((tick) => (
          <span key={tick}>{format.format(tick)}</span>
        ))}
      </span>
      <span />
    </div>
  );
}

function Lanes({
  rows,
  axis,
  now,
  depth,
}: {
  rows: readonly ChildTimelineRow[];
  axis: Axis;
  now: number;
  depth: number;
}) {
  const items = groupLanes(rows);
  return (
    <ol className="ptl-lanes">
      {items.map((item) =>
        item.kind === "row" ? (
          <LaneRow key={item.row.id} row={item.row} axis={axis} now={now} depth={depth} />
        ) : (
          <LaneGroup key={item.key} first={item.first} rest={item.rest} axis={axis} now={now} depth={depth} />
        ),
      )}
    </ol>
  );
}

function LaneGroup({
  first,
  rest,
  axis,
  now,
  depth,
}: {
  first: ChildTimelineRow;
  rest: readonly ChildTimelineRow[];
  axis: Axis;
  now: number;
  depth: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <LaneRow row={first} axis={axis} now={now} depth={depth} />
      {open ? rest.map((row) => <LaneRow key={row.id} row={row} axis={axis} now={now} depth={depth} />) : null}
      <li className="ptl-group">
        <Button variant="plain" compact className="ptl-group-btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Show fewer" : `+ ${rest.length} more at ${nowLabel(first)}`}
        </Button>
      </li>
    </>
  );
}

function LaneRow({ row, axis, now, depth }: { row: ChildTimelineRow; axis: Axis; now: number; depth: number }) {
  const [expanded, setExpanded] = useState(false);
  const tail = idleTail(axis, row, now);
  const tone = nowTone(row);
  return (
    <li className="ptl-item">
      <div className="ptl-row" style={{ paddingLeft: depth * 16 }}>
        <span className="ptl-name">
          {row.child_count > 0 ? (
            <Button
              variant="plain"
              compact
              className="ptl-expand"
              aria-expanded={expanded}
              aria-label={`${expanded ? "Collapse" : "Expand"} ${row.external_id}, ${row.child_count} child ticket${row.child_count === 1 ? "" : "s"}`}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? "▾" : "▸"}
            </Button>
          ) : (
            <span className="ptl-expand-spacer" />
          )}
          <Button
            variant="plain"
            className="ptl-link"
            title={row.title}
            onClick={() => navigateToTicket(row.id)}
          >
            <span className="ptl-ext">{row.external_id}</span>
            <span className="ptl-title">{row.title}</span>
          </Button>
        </span>
        <span className="ptl-lane" aria-hidden="true">
          {row.segments.map((segment, index) => {
            const span = segmentSpan(axis, segment, now);
            if (!span) return null;
            const back = segment.visit_number > 1;
            return (
              <span
                key={`${segment.stage_key}-${index}`}
                className={`ptl-seg ptl-seg--${segmentTone(segment)} ${back ? "ptl-seg--back" : ""}`}
                style={spanStyle(span)}
                title={segmentTitle(segment.stage_name, segment.visit_number, segment.attempts, segment.status, segment.started_at, segment.finished_at)}
              />
            );
          })}
          {tail ? <span className={`ptl-tail ptl-tail--${tone}`} style={spanStyle(tail)} /> : null}
        </span>
        <span className={`ptl-now ptl-now--${tone}`}>
          {nowLabel(row)}
          {tail ? <span className="ptl-age"> · {waitedFor(row, now)}</span> : null}
        </span>
        <span className="ptl-sr">{rowSummary(row)}</span>
      </div>
      {expanded ? <NestedLanes ticketId={row.id} axis={axis} now={now} depth={depth + 1} /> : null}
    </li>
  );
}

/** "8d" since the last visit ended, for a row that is waiting or blocked. */
function waitedFor(row: ChildTimelineRow, now: number): string {
  const last = lastActivity(row, now);
  return last === null ? "" : formatRelativeAge(new Date(last).toISOString(), new Date(now)).replace(" ago", "");
}

function spanStyle(span: Span) {
  return { left: `${span.left}%`, width: `${span.width}%` };
}

function segmentTitle(
  stage: string,
  visit: number,
  attempts: number,
  status: string,
  startedAt: string | null,
  finishedAt: string | null,
): string {
  const when = startedAt ? `${TIME_FORMAT.format(Date.parse(startedAt))} – ${finishedAt ? TIME_FORMAT.format(Date.parse(finishedAt)) : "now"}` : "";
  const again = visit > 1 ? ` (visit ${visit})` : "";
  return `${stage}${again}: ${status}, ${attempts} attempt${attempts === 1 ? "" : "s"}${when ? ` · ${when}` : ""}`;
}

/** A child's own children, laid on the outer axis so the rows still line up. */
function NestedLanes({ ticketId, axis, now, depth }: { ticketId: string; axis: Axis; now: number; depth: number }) {
  const query = useChildrenTimeline(ticketId, false);
  if (query.isPending) return <div className="ptl-nested-state" style={{ paddingLeft: depth * 16 + 24 }}>Loading child tickets…</div>;
  if (query.isError) {
    return (
      <div className="ptl-nested-state" role="alert" style={{ paddingLeft: depth * 16 + 24 }}>
        Could not load child tickets: {describeError(query.error, "the request failed")}{" "}
        <Button variant="secondary" compact onClick={() => void query.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  const sections = sectionChildren(query.data.children);
  const pending = sections.ready.length + sections.waiting.length;
  return (
    <>
      {sections.lanes.length > 0 ? <Lanes rows={sections.lanes} axis={axis} now={now} depth={depth} /> : null}
      {pending > 0 ? (
        <div className="ptl-nested-state" style={{ paddingLeft: depth * 16 + 24 }}>
          {pending} not started
        </div>
      ) : null}
    </>
  );
}

function NotStarted({
  ready,
  waiting,
  closed,
}: {
  ready: readonly ChildTimelineRow[];
  waiting: readonly ChildTimelineRow[];
  closed: readonly ChildTimelineRow[];
}) {
  if (ready.length + waiting.length + closed.length === 0) return null;
  return (
    <section className="ptl-pending" aria-label="Child tickets that have not run">
      {ready.length > 0 ? (
        <PendingList title={`Ready to start · ${ready.length}`} rows={ready} />
      ) : null}
      {waiting.length > 0 ? (
        <PendingList title={`Waiting on other tickets · ${waiting.length}`} rows={waiting} showWaits />
      ) : null}
      {closed.length > 0 ? (
        <PendingList title={`Closed without a run · ${closed.length}`} rows={closed} collapsed />
      ) : null}
    </section>
  );
}

function PendingList({
  title,
  rows,
  showWaits = false,
  collapsed = false,
}: {
  title: string;
  rows: readonly ChildTimelineRow[];
  showWaits?: boolean;
  collapsed?: boolean;
}) {
  // Over a dozen names is a wall; the heading's count says enough until asked.
  const [open, setOpen] = useState(!collapsed && rows.length <= COLLAPSE_LIST_AT);
  const headingId = useId();
  return (
    <div className="ptl-pending-group">
      <Button
        variant="plain"
        compact
        className="ptl-pending-head"
        id={headingId}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "▾" : "▸"} {title}
      </Button>
      {open ? (
        <ul className="ptl-pending-list" aria-labelledby={headingId}>
          {rows.map((row) => (
            <li key={row.id}>
              <Button variant="plain" className="ptl-link" title={row.title} onClick={() => navigateToTicket(row.id)}>
                <span className="ptl-ext">{row.external_id}</span>
                <span className="ptl-title">{row.title}</span>
              </Button>
              {showWaits ? (
                <span className="ptl-waits">
                  waits on{" "}
                  {row.waiting_on.slice(0, 3).map((dep, index) => (
                    <span key={dep.id}>
                      {index > 0 ? ", " : ""}
                      <Button variant="plain" className="ptl-dep" title={dep.title} onClick={() => navigateToTicket(dep.id)}>
                        {dep.external_id}
                      </Button>
                    </span>
                  ))}
                  {row.waiting_on.length > 3 ? ` +${row.waiting_on.length - 3} more` : ""}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
