
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { AutopilotTicketRef, SubtreeAutopilot, WorkItemType } from "../api/client";
import { AUTOPILOT_ACTION_LABEL, AUTOPILOT_PARALLEL_CHOICES, formatWhen } from "../lib/scheduleFormat";
import { workItemTypeLabel } from "../lib/workItemHierarchy";
import { describeError } from "../state/toastStore";
import { Button } from "./ui/Button";
import { Select } from "./ui/Select";
import "./SubtreeAutopilotCard.css";

/** Past this many, a list says "+N more" — a milestone can hold ~90 items. */
const LIST_LIMIT = 5;

const subtreeAutopilotKey = (ticketId: string) => ["ticket-autopilot", ticketId] as const;

function RefList({
  title,
  refs,
  ordered = false,
  onOpen,
}: {
  title: string;
  refs: AutopilotTicketRef[];
  ordered?: boolean;
  onOpen: (id: string) => void;
}) {
  if (refs.length === 0) return null;
  const shown = refs.slice(0, LIST_LIMIT);
  const List = ordered ? "ol" : "ul";
  return (
    <div>
      <h4 className="subtree-autopilot-subtitle">
        {title} ({refs.length})
      </h4>
      <List className="subtree-autopilot-list">
        {shown.map((ref) => (
          <li key={ref.id}>
            <Button
              variant="plain"
              className="subtree-autopilot-link"
              title={`Open ${ref.external_id}`}
              onClick={() => onOpen(ref.id)}
            >
              <span className="subtree-autopilot-mono">{ref.external_id}</span> {ref.title}
            </Button>
          </li>
        ))}
      </List>
      {refs.length > shown.length ? (
        <p className="subtree-autopilot-muted">+{refs.length - shown.length} more in the child tickets below</p>
      ) : null}
    </div>
  );
}

function statusLine(view: SubtreeAutopilot, kind: string): string {
  const { autopilot } = view;
  const open = `${view.open_items} of ${view.total_items} item${view.total_items === 1 ? "" : "s"} open`;
  if (autopilot.enabled) {
    return `On — ${autopilot.in_flight} of ${autopilot.max_parallel} running, ${open}. It queues ready items every minute, critical path first.`;
  }
  return `Off. Turn it on to run this ${kind}'s ready items in parallel as their prerequisites land — never work marked for a person. ${open}.`;
}

/**
 * The autopilot for a milestone or parent ticket — the initiative autopilot,
 * rooted here.
 *
 * Answers "is something running this subtree's work for me, and what does it
 * start next?". The actions are the on/off switch, the parallel cap (applied
 * with Update, so a mis-pick changes nothing), and each named ticket, which
 * opens it.
 */
export function SubtreeAutopilotCard({
  ticketId,
  workItemType,
  onOpenTicket,
}: {
  ticketId: string;
  workItemType: WorkItemType;
  onOpenTicket: (id: string) => void;
}) {
  const qc = useQueryClient();
  const kind = workItemTypeLabel(workItemType).toLowerCase();

  const query = useQuery({
    queryKey: subtreeAutopilotKey(ticketId),
    queryFn: () => api.ticketAutopilot(ticketId),
    // What is running and ready moves whenever an item lands.
    refetchInterval: 30_000,
    // The card renders its own failure, with a retry.
    meta: { suppressErrorToast: true },
  });

  const change = useMutation({
    meta: { errorTitle: "Change the autopilot" },
    mutationFn: (body: { enabled?: boolean; max_parallel?: number }) => api.setTicketAutopilot(ticketId, body),
    onSuccess: (next) => qc.setQueryData(subtreeAutopilotKey(ticketId), next),
  });

  const view = query.data;
  const busy = change.isPending;

  if (!view) {
    return (
      <section className="state-card subtree-autopilot" aria-labelledby="subtree-autopilot-title" aria-busy={query.isPending}>
        <h3 id="subtree-autopilot-title" className="state-label">
          Autopilot
        </h3>
        {query.isError ? (
          <div className="subtree-autopilot-row">
            <p className="subtree-autopilot-alert" role="alert">
              Could not load the autopilot: {describeError(query.error, "the request failed")}
            </p>
            <Button variant="secondary" compact onClick={() => void query.refetch()}>
              Retry
            </Button>
          </div>
        ) : (
          <div className="subtree-autopilot-skeleton" aria-hidden />
        )}
      </section>
    );
  }

  const { autopilot, running_above: runningAbove } = view;
  const toggleTitle = !autopilot.available
    ? "The autopilot does not run on a sandbox server"
    : autopilot.enabled
      ? "Stop starting new work; what is running carries on"
      : `Start this ${kind}'s ready items, up to ${autopilot.max_parallel} at once`;

  return (
    <section className="state-card subtree-autopilot" aria-labelledby="subtree-autopilot-title" aria-busy={busy}>
      <div className="subtree-autopilot-head">
        <div>
          <h3 id="subtree-autopilot-title" className="state-label">
            Autopilot {autopilot.enabled ? "· on" : ""}
          </h3>
          <p className="subtree-autopilot-muted">{statusLine(view, kind)}</p>
        </div>
        <div className="subtree-autopilot-controls">
          <Select
            aria-label="Most items the autopilot runs at once"
            className="subtree-autopilot-select"
            value={String(autopilot.max_parallel)}
            disabled={busy}
            // Saved on pick: a draft held until "Update" was dropped by "Turn on",
            // so the select showed 1 while the autopilot ran 2.
            onChange={(e) => change.mutate({ max_parallel: Number(e.target.value) })}
          >
            {AUTOPILOT_PARALLEL_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n} at once
              </option>
            ))}
          </Select>
          <Button
            variant={autopilot.enabled ? "secondary" : "primary"}
            compact
            aria-pressed={autopilot.enabled}
            disabled={busy || !autopilot.available}
            title={toggleTitle}
            onClick={() => change.mutate({ enabled: !autopilot.enabled })}
          >
            {busy ? "Saving…" : autopilot.enabled ? "Turn off" : "Turn on"}
          </Button>
        </div>
      </div>

      {autopilot.available ? null : (
        <p className="subtree-autopilot-hint">
          This is a sandbox server on a copy of the database, so the autopilot is off and starts no agent runs here.
        </p>
      )}

      {autopilot.paused_reason ? (
        <p className="subtree-autopilot-alert" role="alert">
          Stopped itself: {autopilot.paused_reason}
        </p>
      ) : null}

      {runningAbove ? (
        <p className="subtree-autopilot-hint">
          <Button
            variant="plain"
            className="subtree-autopilot-link"
            title={`Open ${runningAbove.external_id}`}
            onClick={() => onOpenTicket(runningAbove.id)}
          >
            <span className="subtree-autopilot-mono">{runningAbove.external_id}</span>
          </Button>{" "}
          is running its own workflow, which works through these items. The autopilot starts nothing here until it
          finishes.
        </p>
      ) : null}

      {view.also_driven_by.length > 0 ? (
        <p className="subtree-autopilot-hint">
          Also started by the autopilot of{" "}
          {view.also_driven_by.map((ref, i) => (
            <span key={ref.id}>
              {i > 0 ? ", " : ""}
              <Button
                variant="plain"
                className="subtree-autopilot-link"
                title={`Open ${ref.external_id}`}
                onClick={() => onOpenTicket(ref.id)}
              >
                <span className="subtree-autopilot-mono">{ref.external_id}</span>
              </Button>
            </span>
          ))}
          . Each one's cap counts everything running under it, so neither runs past its own.
        </p>
      ) : null}

      {autopilot.enabled || view.waiting_on_person.length > 0 || view.blocked.length > 0 ? (
        <>
          <RefList title="Starts next" refs={view.next_up} ordered onOpen={onOpenTicket} />
          {autopilot.enabled && view.next_up.length === 0 && view.open_items > 0 ? (
            <p className="subtree-autopilot-muted">
              Nothing to start right now: every open item is running, waiting on prerequisites, or waiting on a person.
            </p>
          ) : null}
          <RefList title="Waiting on a person" refs={view.waiting_on_person} onOpen={onOpenTicket} />
          <RefList title="Blocked" refs={view.blocked} onOpen={onOpenTicket} />
        </>
      ) : null}

      {autopilot.recent.length > 0 ? (
        <details className="subtree-autopilot-log">
          <summary>What it did recently</summary>
          <ul className="subtree-autopilot-list">
            {autopilot.recent.map((event, i) => (
              <li key={`${event.created_at}-${i}`}>
                <span className="subtree-autopilot-muted">{formatWhen(event.created_at)}</span>{" "}
                {AUTOPILOT_ACTION_LABEL[event.action]}
                {event.ticket_external_id ? <span className="subtree-autopilot-mono"> {event.ticket_external_id}</span> : null}
                {event.detail ? <span className="subtree-autopilot-muted"> — {event.detail}</span> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
