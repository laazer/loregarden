import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import type { InitiativePlan, PlanNode } from "../../../api/initiativeApi";
import {
  LABEL_INSIDE_MIN_PX,
  NODE_STATUS_COLOR,
  NODE_STATUS_LABEL,
  PX_PER_DAY,
  ganttGroups,
  ganttRange,
  ganttTicks,
  linkPath,
  nodeSpan,
  xOf,
  type GanttGroup,
  type GanttRange,
  type GanttScale,
  type Span,
} from "../../../lib/ganttLayout";
import { SCHEDULE_STATUS_COLOR, SCHEDULE_STATUS_LABEL, isoDay } from "../../../lib/scheduleFormat";
import { peekTicket } from "../../../state/ticketPeekStore";
import { Button } from "../../ui/Button";
import { Input } from "../../ui/Input";

const ROW_H = 30;
/** A phase with more work than this starts folded, its count on the toggle: open, it is a wall of rows. */
const OPEN_MAX = 12;
const SCALES: [GanttScale, string][] = [
  ["day", "Day"],
  ["week", "Week"],
  ["month", "Month"],
];

function day(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

function spanText(span: Span | null): string {
  return span ? `${day(span.start)} – ${day(span.finish)}` : "not scheduled";
}

type Row =
  | { kind: "group"; group: GanttGroup }
  | { kind: "node"; node: PlanNode; group: GanttGroup };

/** A bar drawn over the track. Mouse only: the row's label is the keyboard's way to the same ticket. */
function Bar({
  span,
  range,
  scale,
  color,
  label,
  className,
  onOpen,
}: {
  span: Span;
  range: GanttRange;
  scale: GanttScale;
  color: string;
  label: ReactNode;
  className: string;
  onOpen?: () => void;
}) {
  const left = xOf(span.start, range, scale);
  const width = Math.max(4, xOf(span.finish, range, scale) - left);
  const inside = width >= LABEL_INSIDE_MIN_PX;
  const style = { left, width, "--bar": color } as CSSProperties;
  return (
    <>
      {onOpen ? (
        // ux-ok: decorative duplicate of the row label's button, which carries the name and the dates
        <Button
          variant="plain"
          className={className}
          style={style}
          tabIndex={-1}
          aria-hidden
          onClick={onOpen}
        >
          {inside ? label : null}
        </Button>
      ) : (
        <span className={className} style={style} aria-hidden>
          {inside ? label : null}
        </span>
      )}
      {inside ? null : (
        <span className="plan-gantt-after" style={{ left: left + width + 6 }} aria-hidden>
          {label}
        </span>
      )}
    </>
  );
}

/**
 * The plan as a Gantt chart: every phase as a span, the work under it as bars,
 * prerequisites as the lines between them, against today and each target.
 *
 * Answers "what runs when, and what is each piece waiting for?". The dates are
 * the dependency graph's forecast (done work where it ran), not something to
 * drag: a phase's target is set in Schedule, and the forecast follows. A row's
 * name opens its ticket.
 */
export function InitiativeGantt({ plan }: { plan: InitiativePlan }) {
  const [scale, setScale] = useState<GanttScale>("week");
  const [query, setQuery] = useState("");
  const [hideDone, setHideDone] = useState(true);
  const groups = useMemo(() => ganttGroups(plan), [plan]);
  const [open, setOpen] = useState<ReadonlySet<string>>(
    () =>
      new Set(
        groups
          .filter((g) => g.children.length > 0 && g.children.length <= OPEN_MAX && g.phase?.status !== "done")
          .map((g) => g.key),
      ),
  );
  const now = Date.parse(plan.generated_at);
  const range = useMemo(() => ganttRange(plan, now), [plan, now]);
  const ticks = useMemo(() => ganttTicks(range, scale), [range, scale]);
  const width = range.days * PX_PER_DAY[scale];
  const paced = plan.mode === "pace";
  const critical = useMemo(() => new Set(plan.critical_path), [plan.critical_path]);
  const scroller = useRef<HTMLDivElement>(null);

  // Open on today, whatever the zoom: the past is mostly done work.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = Math.max(0, xOf(now, range, scale) - 160);
  }, [now, range, scale]);

  const needle = query.trim().toLowerCase();
  const matches = (node: PlanNode) =>
    !needle || node.external_id.toLowerCase().includes(needle) || node.title.toLowerCase().includes(needle);

  const rows: Row[] = [];
  for (const group of groups) {
    if (hideDone && group.phase?.status === "done") continue;
    const phaseHit = group.phase
      ? !needle ||
        group.phase.external_id.toLowerCase().includes(needle) ||
        group.phase.title.toLowerCase().includes(needle)
      : !needle;
    const kids = group.children.filter((n) => phaseHit || matches(n));
    if (!phaseHit && kids.length === 0) continue;
    rows.push({ kind: "group", group });
    // A search opens every phase it finds work in.
    if (open.has(group.key) || (needle && !phaseHit)) {
      for (const node of kids) rows.push({ kind: "node", node, group });
    }
  }

  // Where each node is drawn: its own row, or folded into its phase's.
  const rowOf = new Map<string, number>();
  rows.forEach((row, index) => {
    if (row.kind === "node") rowOf.set(row.node.id, index);
    else {
      if (row.group.self) rowOf.set(row.group.self.id, index);
      for (const node of row.group.children) if (!rowOf.has(node.id)) rowOf.set(node.id, index);
    }
  });
  const nodeById = new Map(plan.nodes.map((n) => [n.id, n]));
  const links: { key: string; d: string; hot: boolean }[] = [];
  const drawn = new Set<string>();
  for (const [id, toRow] of rowOf) {
    const node = nodeById.get(id);
    const to = node && nodeSpan(node);
    if (!node || !to) continue;
    for (const depId of node.deps) {
      const fromRow = rowOf.get(depId);
      const dep = nodeById.get(depId);
      const from = dep && nodeSpan(dep);
      if (fromRow === undefined || !from || fromRow === toRow) continue;
      const key = `${fromRow}:${toRow}`;
      if (drawn.has(key)) continue;
      drawn.add(key);
      links.push({
        key,
        hot: critical.has(id) && critical.has(depId),
        d: linkPath(
          { x: xOf(from.finish, range, scale), y: fromRow * ROW_H + ROW_H / 2 },
          { x: xOf(to.start, range, scale), y: toRow * ROW_H + ROW_H / 2 },
          ROW_H,
        ),
      });
    }
  }

  const toggle = (key: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const expandable = groups.filter((g) => g.children.length > 0).map((g) => g.key);
  const filtered = Boolean(needle) || hideDone;

  if (!plan.nodes.some((n) => nodeSpan(n))) {
    return (
      <div className="plan-empty">
        <p>
          Nothing is scheduled yet. The chart draws each ticket once the plan can forecast it — attach a milestone with
          work under it, or track a ticket from Schedule.
        </p>
      </div>
    );
  }

  return (
    <section className="plan-gantt" aria-label="Gantt chart">
      <div className="plan-gantt-toolbar">
        <div className="plan-gantt-scales" role="group" aria-label="Zoom">
          {SCALES.map(([key, label]) => (
            <Button
              key={key}
              variant="secondary"
              compact
              aria-pressed={scale === key}
              className={scale === key ? "active" : undefined}
              onClick={() => setScale(key)}
            >
              {label}
            </Button>
          ))}
        </div>
        <Input
          type="search"
          aria-label="Filter the chart by id or title"
          className="filter-select plan-gantt-filter"
          placeholder="Filter by id or title"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="plan-gantt-check">
          <Input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} />
          Hide finished phases
        </label>
        <Button variant="plain" className="plan-inline-btn" onClick={() => setOpen(new Set(expandable))}>
          Expand all
        </Button>
        <Button variant="plain" className="plan-inline-btn" onClick={() => setOpen(new Set())}>
          Collapse all
        </Button>
      </div>

      <p className="plan-muted plan-legend" aria-hidden>
        {(["done", "running", "ready", "waiting", "needs_person", "blocked"] as const).map((status) => (
          <span key={status}>
            <span className="plan-gantt-swatch" style={{ "--bar": NODE_STATUS_COLOR[status] } as CSSProperties} />{" "}
            {NODE_STATUS_LABEL[status]}
          </span>
        ))}
        <span>
          <span className="plan-gantt-swatch plan-gantt-swatch-critical" /> critical path
        </span>
        <span>
          <span className="plan-gantt-swatch plan-gantt-swatch-today" /> today
        </span>
        {paced ? null : (
          <span>
            <span className="plan-legend-target" /> target
          </span>
        )}
      </p>

      {rows.length === 0 ? (
        <div className="plan-empty">
          <p>{needle ? `Nothing matches “${query.trim()}”.` : "Every phase is finished."}</p>
          {filtered ? (
            <Button
              variant="secondary"
              compact
              onClick={() => {
                setQuery("");
                setHideDone(false);
              }}
            >
              Show everything
            </Button>
          ) : null}
        </div>
      ) : (
        <div
          className="plan-gantt-scroll"
          ref={scroller}
          role="region"
          aria-label="Chart, scrolls sideways through time"
          tabIndex={0}
        >
          <div className="plan-gantt-head" style={{ width: `calc(var(--gantt-label) + ${width}px)` }}>
            <div className="plan-gantt-label plan-gantt-corner">Ticket</div>
            <div className="plan-gantt-scale" style={{ width }} aria-hidden>
              <div className="plan-gantt-tier">
                {ticks.upper.map((t) => (
                  <span key={t.key} style={{ left: t.left, width: t.width }}>
                    {t.label}
                  </span>
                ))}
              </div>
              <div className="plan-gantt-tier">
                {ticks.lower.map((t) => (
                  <span key={t.key} className={t.weekend ? "weekend" : undefined} style={{ left: t.left, width: t.width }}>
                    {t.label}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div
            className="plan-gantt-body"
            style={{ width: `calc(var(--gantt-label) + ${width}px)`, height: rows.length * ROW_H }}
          >
            <div className="plan-gantt-grid" style={{ width }} aria-hidden>
              {ticks.lower.map((t) => (
                <span
                  key={t.key}
                  className={t.weekend ? "weekend" : undefined}
                  style={{ left: t.left, width: t.width }}
                />
              ))}
              <span className="plan-gantt-today" style={{ left: xOf(now, range, scale) }} />
            </div>
            <svg className="plan-gantt-links" width={width} height={rows.length * ROW_H} aria-hidden>
              <defs>
                <marker id="plan-gantt-arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0,0 L6,3 L0,6 z" className="plan-gantt-arrowhead" />
                </marker>
              </defs>
              {links.map((link) => (
                <path
                  key={link.key}
                  d={link.d}
                  className={link.hot ? "plan-gantt-link hot" : "plan-gantt-link"}
                  markerEnd="url(#plan-gantt-arrow)"
                />
              ))}
            </svg>

            <ul className="plan-gantt-rows" aria-label="Phases and their work">
              {rows.map((row) =>
                row.kind === "group" ? (
                  <GroupRow
                    key={`g-${row.group.key}`}
                    group={row.group}
                    open={open.has(row.group.key) || Boolean(needle)}
                    onToggle={() => toggle(row.group.key)}
                    range={range}
                    scale={scale}
                    paced={paced}
                    critical={critical}
                  />
                ) : (
                  <NodeRow
                    key={`n-${row.group.key}-${row.node.id}`}
                    node={row.node}
                    range={range}
                    scale={scale}
                    critical={critical.has(row.node.id)}
                  />
                ),
              )}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}

function NodeRow({
  node,
  range,
  scale,
  critical,
}: {
  node: PlanNode;
  range: GanttRange;
  scale: GanttScale;
  critical: boolean;
}) {
  const span = nodeSpan(node);
  const descId = `plan-gantt-d-${node.id}`;
  const label = (
    <>
      <span className="plan-mono">{node.external_id}</span> {node.title}
    </>
  );
  return (
    <li className={`plan-gantt-row${node.status === "done" ? " plan-row-done" : ""}`}>
      <div className="plan-gantt-label plan-gantt-child">
        <Button
          variant="plain"
          className="plan-link plan-gantt-name"
          title={`${node.external_id} ${node.title}`}
          aria-describedby={descId}
          onClick={() => peekTicket(node.id)}
        >
          {label}
        </Button>
        <span id={descId} className="plan-sr-only">
          {spanText(span)}, {NODE_STATUS_LABEL[node.status]}
          {critical ? ", on the critical path" : ""}
          {node.external ? `, in ${node.workspace_slug}, outside this initiative` : ""}
        </span>
      </div>
      <div className="plan-gantt-track">
        {span ? (
          <Bar
            span={span}
            range={range}
            scale={scale}
            color={NODE_STATUS_COLOR[node.status]}
            label={label}
            className={`plan-gantt-bar${critical ? " critical" : ""}`}
            onOpen={() => peekTicket(node.id)}
          />
        ) : null}
      </div>
    </li>
  );
}

function GroupRow({
  group,
  open,
  onToggle,
  range,
  scale,
  paced,
  critical,
}: {
  group: GanttGroup;
  open: boolean;
  onToggle: () => void;
  range: GanttRange;
  scale: GanttScale;
  paced: boolean;
  critical: ReadonlySet<string>;
}) {
  const { phase, self, span } = group;
  const single = self !== null && group.children.length === 0;
  const name = phase ? `${phase.external_id} ${phase.title}` : "Outside this initiative";
  const descId = `plan-gantt-d-${group.key}`;
  const label = phase ? (
    <>
      <span className="plan-mono">{phase.external_id}</span> {phase.title}
    </>
  ) : (
    "Outside this initiative"
  );
  const status = single && self ? NODE_STATUS_LABEL[self.status] : phase ? SCHEDULE_STATUS_LABEL[phase.status] : "";
  const color = single && self ? NODE_STATUS_COLOR[self.status] : phase ? SCHEDULE_STATUS_COLOR[phase.status] : "var(--txl)";
  const target = phase?.target_date && !paced ? isoDay(phase.target_date) : null;
  return (
    <li className={`plan-gantt-row plan-gantt-group${phase?.status === "done" ? " plan-row-done" : ""}`}>
      <div className="plan-gantt-label">
        {group.children.length > 0 ? (
          <Button
            variant="plain"
            className="plan-icon-btn plan-tree-toggle"
            aria-expanded={open}
            aria-label={`${open ? "Hide" : "Show"} the ${group.children.length} in ${phase?.external_id ?? "outside work"}`}
            onClick={onToggle}
          >
            {open ? "▾" : "▸"}
          </Button>
        ) : (
          <span className="plan-tree-spacer" aria-hidden />
        )}
        {phase ? (
          <Button
            variant="plain"
            className="plan-link plan-gantt-name"
            title={name}
            aria-describedby={descId}
            onClick={() => peekTicket(phase.id)}
          >
            {label}
          </Button>
        ) : (
          <span className="plan-gantt-name">
            {label}
          </span>
        )}
        <span id={descId} className="plan-sr-only">
          {spanText(span)}
          {status ? `, ${status}` : ""}
          {group.children.length > 0 ? `, ${group.children.length} tickets` : ""}
        </span>
      </div>
      <div className="plan-gantt-track">
        {span ? (
          <Bar
            span={span}
            range={range}
            scale={scale}
            color={color}
            label={label}
            className={
              single
                ? `plan-gantt-bar${self && critical.has(self.id) ? " critical" : ""}`
                : "plan-gantt-bar plan-gantt-summary"
            }
            onOpen={phase ? () => peekTicket(phase.id) : undefined}
          />
        ) : null}
        {target !== null ? (
          <span className="plan-gantt-target" style={{ left: xOf(target, range, scale) }} aria-hidden />
        ) : null}
      </div>
    </li>
  );
}
