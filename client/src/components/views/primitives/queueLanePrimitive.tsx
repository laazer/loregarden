/**
 * One execution lane, as a pane.
 *
 * A lane is a serial pipeline holding one slot: what is running in it now, and
 * what is waiting behind it. The Queue Dashboard shows all of them at once and
 * is page furniture — `ParallelQueueVisualization.css` pins a `min-height:
 * 389px` rail and a `max-height: 320px` list, which is right on a page and
 * wrong in a pane the grid may have made 120px tall. That is why the queue was
 * ruled out of 436's registry, and why this is a purpose-built pane rather than
 * that component in a smaller box.
 *
 * ## One request, however many panes
 *
 * Through `useQueueLanes`, a shared react-query key. Not `useQueueStatus`: a
 * container primitive may not import from `state/` — the rule
 * `containerPrimitives.render` enforces — because a context read outside its
 * provider returns an empty value rather than throwing, and a pane silently
 * showing nothing is exactly the failure that rule prevents. Not
 * `useParallelExecutionWS` either, which opens a socket per call. A grid with
 * one pane per lane is the obvious layout for this primitive, and three panes
 * must not mean three of anything.
 *
 * ## It lays out for the pane, not for a page
 *
 * `compact` drops the waiting list to a count and the running card to its code:
 * a pane 120px tall cannot show a list, and a scrollbar over three clipped rows
 * is worse than an honest "3 waiting". `wide` is where the ticket titles and
 * the attention rail have room to sit beside each other. This is the first
 * consumer of `usePaneSize`, and the reason it exists.
 */

import { useQueueLanes } from "../../../hooks/useQueueLanes";
import type { LaneEntry, QueueLane } from "../../../lib/queueSocket";
import { usePaneSize } from "../paneSize";
import { definePrimitive } from "./definePrimitive";
import { Unconfigured } from "./Unconfigured";
import "./queueLane.css";

type QueueLaneSettings = {
  /** The slot number this pane watches. `0` means none has been chosen. */
  slot: number;
};

/** The ticket a lane entry is for, as a person would name it. */
function entryLabel(entry: { ticket_code?: string; ticket_title?: string }): string {
  const code = entry.ticket_code ?? "";
  const title = entry.ticket_title ?? "";
  if (code !== "" && title !== "") return `${code} · ${title}`;
  if (code !== "") return code;
  return title === "" ? "an unnamed ticket" : title;
}

/** `137` → `2m 17s`, and `0` → `0s`. Whole seconds; a lane is not a stopwatch. */
export function elapsedLabel(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  if (minutes === 0) return `${whole}s`;
  const hours = Math.floor(minutes / 60);
  if (hours === 0) return `${minutes}m ${whole % 60}s`;
  return `${hours}h ${minutes % 60}m`;
}

function WaitingList({ waiting }: { waiting: LaneEntry[] }) {
  return (
    <ol className="queue-lane-waiting" aria-label="Waiting in this lane">
      {waiting.map((entry) => (
        <li key={entry.entry_id} className="queue-lane-waiting-row">
          <span className="queue-lane-position">{entry.position}</span>
          <span className="queue-lane-waiting-name">{entryLabel(entry)}</span>
        </li>
      ))}
    </ol>
  );
}

function LaneBody({ lane }: { lane: QueueLane }) {
  const { tier } = usePaneSize();
  const waiting = lane.waiting ?? [];
  const attention = lane.attention_total ?? lane.attention?.length ?? 0;

  return (
    <div className="queue-lane" data-slot={lane.slot_number}>
      <div className="queue-lane-head">
        <span className="queue-lane-slot">Lane {lane.slot_number}</span>
        <span
          className="queue-lane-state"
          data-running={lane.running === null ? "false" : "true"}
        >
          {lane.running === null ? "Idle" : "Running"}
        </span>
      </div>

      {lane.running === null ? (
        <p className="queue-lane-empty">Nothing is running in this lane.</p>
      ) : (
        <div className="queue-lane-running">
          <span className="queue-lane-running-name">
            {tier === "compact"
              ? (lane.running.ticket_code ?? entryLabel(lane.running))
              : entryLabel(lane.running)}
          </span>
          <span className="queue-lane-running-meta">
            {elapsedLabel(lane.running.elapsed_seconds)}
            {tier === "compact" ? "" : ` · ${lane.running.status}`}
          </span>
        </div>
      )}

      {/* A count at every tier, because it is the fact; the list only where
          there is room to read one. */}
      <div className="queue-lane-waiting-head">
        {waiting.length === 1 ? "1 waiting" : `${waiting.length} waiting`}
        {attention === 0 ? "" : ` · ${attention} needing attention`}
      </div>
      {tier === "compact" || waiting.length === 0 ? null : <WaitingList waiting={waiting} />}
    </div>
  );
}

export const queueLanePrimitive = definePrimitive<QueueLaneSettings>({
  id: "queue_lane",
  displayName: "Queue Lane",
  icon: "▤",
  category: "Queue",
  containerKind: "panel",
  settingsFields: [
    {
      key: "slot",
      kind: "choice",
      source: "lane",
      label: "Lane",
      default: "",
      help: "The execution lane this pane watches.",
    },
  ],
  parseSettings: (raw) => {
    // Stored as the choice's string, because `choice` is a string field — the
    // whole point of that kind is that it stores what a text box stored. The
    // number is this primitive's business, and `0` is "none chosen", which no
    // real slot number is.
    const stored = typeof raw.slot === "string" ? Number(raw.slot) : raw.slot;
    return {
      slot: typeof stored === "number" && Number.isInteger(stored) && stored > 0 ? stored : 0,
    };
  },
  Component: ({ settings }) => {
    const { lanes, isLoading } = useQueueLanes(settings.slot !== 0);

    if (settings.slot === 0) {
      return <Unconfigured>This pane has no lane yet.</Unconfigured>;
    }
    if (isLoading) {
      return <Unconfigured>Reading the queue…</Unconfigured>;
    }

    const lane = lanes.find((candidate) => candidate.slot_number === settings.slot);
    if (lane === undefined) {
      // Distinguished from "no lane chosen": the pool is configurable, and a
      // view naming lane 4 after the pool shrank to three should say which of
      // the two things is wrong.
      return <Unconfigured>{`Lane ${settings.slot} is not in the pool.`}</Unconfigured>;
    }
    return <LaneBody lane={lane} />;
  },
});
