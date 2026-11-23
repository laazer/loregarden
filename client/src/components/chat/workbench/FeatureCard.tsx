import { useState } from "react";

import type {
  FeatureCriterion,
  FeatureCriterionStatus,
  FeatureLoop,
  FeatureLoopStartRequest,
} from "../../../api/chatWorkbenchTypes";
import { TicketLink } from "../../TicketLink";
import { Button } from "../../ui/Button";
import { Input } from "../../ui/Input";
import { PaneSkeleton } from "../../ui/PaneSkeleton";
import { FeatureLoopStartModal } from "./FeatureLoopStartModal";
import { WorkbenchCard } from "./WorkbenchCard";

/** Waiting on the operator first: that is what this card exists to surface. */
const GROUPS: { status: FeatureCriterionStatus; label: string }[] = [
  { status: "proposed", label: "Proposed — waiting on you" },
  { status: "queued", label: "Queued for the next round" },
  { status: "in_round", label: "In this round" },
  { status: "met", label: "Met" },
];

const LOOP_STATUS_LABEL: Record<FeatureLoop["status"], string> = {
  idle: "waiting",
  running: "running",
  blocked: "stalled",
};

/**
 * "What is this feature asked to do, what is waiting on my yes or no, and can
 * the ticket still take it?" The loop's criteria by stage, Accept / Drop on each
 * proposal, and one field to add a criterion yourself. Without a loop, the way
 * to start one.
 *
 * At the cap the third question's answer is no, so the card says so inline —
 * above the groups and above every control it disables — and the controls carry
 * the HTML `disabled` attribute rather than a style, so they leave the tab order
 * and the ticket link the message names is the last control a keyboard reaches.
 */
export function FeatureCard({
  feature,
  loading = false,
  workspaces,
  defaultWorkspace,
  busy,
  starting,
  onStart,
  onDecide,
  onAdd,
}: {
  feature: FeatureLoop | null;
  /**
   * The thread snapshot is still on the wire. Without it, `feature: snapshot
   * ?.feature ?? null` collapses "not loaded" into "no loop" and a thread that
   * already has one flashes the start-a-loop empty state on every cold open.
   */
  loading?: boolean;
  workspaces: string[];
  defaultWorkspace: string;
  /** A decision or an add is in flight: every write control waits for it. */
  busy: boolean;
  starting: boolean;
  onStart: (body: FeatureLoopStartRequest) => Promise<unknown>;
  onDecide: (ids: string[], accept: boolean) => void;
  onAdd: (text: string) => Promise<unknown>;
}) {
  const [startOpen, setStartOpen] = useState(false);
  const [draft, setDraft] = useState("");

  if (!feature) {
    if (loading) {
      return (
        <WorkbenchCard title="Feature">
          <PaneSkeleton variant="list" rows={3} label="Loading this chat's feature loop…" />
        </WorkbenchCard>
      );
    }
    return (
      <WorkbenchCard title="Feature">
        <p className="chat-side-card-empty">
          Turn this chat into a feature: Baxter drafts acceptance criteria as you talk, you
          approve them, and the ticket&apos;s workflow builds each batch.
        </p>
        <Button
          variant="secondary"
          compact
          aria-haspopup="dialog"
          aria-expanded={startOpen}
          disabled={!defaultWorkspace}
          onClick={() => setStartOpen(true)}
        >
          Start a feature loop…
        </Button>
        {startOpen ? (
          <FeatureLoopStartModal
            open={startOpen}
            workspaces={workspaces}
            defaultWorkspace={defaultWorkspace}
            saving={starting}
            onStart={onStart}
            onClose={() => setStartOpen(false)}
          />
        ) : null}
      </WorkbenchCard>
    );
  }

  const byStatus = (status: FeatureCriterionStatus) =>
    feature.criteria.filter((c) => c.status === status);
  const proposed = byStatus("proposed");
  const met = byStatus("met").length;
  const { ticket, capacity } = feature;
  // The server's reading, not arithmetic over the counts: `would_grow_past`
  // answers "at cap" on a ticket already past it, where `on_ticket + queued >=
  // max` would read as room. Either flag alone disables, so the cap message
  // never appears or vanishes because a click is in flight.
  const atCap = capacity.at_cap;
  const frozen = busy || atCap;

  return (
    <WorkbenchCard
      title="Feature"
      meta={
        <span className={`chat-wb-status chat-wb-status--${feature.status}`}>
          {feature.round ? `round ${feature.round} · ` : ""}
          {LOOP_STATUS_LABEL[feature.status]}
        </span>
      }
    >
      <p className="chat-wb-ticket">
        <TicketLink ticketId={ticket.id} className="chat-wb-row-link" state={ticket.state}>
          {ticket.title}
        </TicketLink>
        <span className="chat-wb-tag">{ticket.external_id}</span>
        <span className="chat-wb-tag">{ticket.state.replace("_", " ")}</span>
      </p>
      {/* At the cap both strings are _capacity_message's output over the same counts, so
          they are byte-identical and one of them is a stutter. Compared rather than
          suppressed on at_cap, because reason also carries the blocked-ticket and
          stopped-round assessments, which must still render at the cap. */}
      {feature.reason && feature.reason !== capacity.message ? (
        <p className="chat-wb-warn" role="status">
          {feature.reason}
        </p>
      ) : null}
      {atCap && capacity.message ? (
        <p className="chat-wb-warn" role="status">
          {capacity.message}
        </p>
      ) : null}
      {feature.criteria.length === 0 ? (
        <p className="chat-side-card-empty">
          No criteria yet. Describe what the feature should do and Baxter will propose some, or add
          one below.
        </p>
      ) : (
        GROUPS.map(({ status, label }) => {
          const rows = byStatus(status);
          if (!rows.length) return null;
          return (
            <div key={status} className="chat-wb-group">
              <div className="chat-side-card-row">
                <h4>
                  {label} <span className="chat-wb-count">{rows.length}</span>
                </h4>
                {status === "proposed" && rows.length > 1 ? (
                  <Button
                    variant="secondary"
                    compact
                    disabled={frozen}
                    onClick={() => onDecide(rows.map((r) => r.id), true)}
                  >
                    Accept all
                  </Button>
                ) : null}
              </div>
              <ul className="chat-wb-criteria">
                {rows.map((criterion) => (
                  <CriterionRow
                    key={criterion.id}
                    criterion={criterion}
                    busy={busy}
                    atCap={atCap}
                    onDecide={onDecide}
                  />
                ))}
              </ul>
            </div>
          );
        })
      )}
      <form
        className="chat-wb-add"
        onSubmit={(event) => {
          event.preventDefault();
          const text = draft.trim();
          // Not only the Queue button: Enter in the field submits the form
          // without going near it, and `add_criterion` calls the same
          // `_check_capacity` with adding=1.
          if (!text || frozen) return;
          void onAdd(text).then(
            () => setDraft(""),
            () => {
              /* silent-ok: the mutation's errorTitle toast reports it; the draft stays to retry */
            },
          );
        }}
      >
        <Input
          aria-label="Add an acceptance criterion"
          placeholder="Add a criterion yourself…"
          value={draft}
          disabled={frozen}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button type="submit" variant="secondary" compact disabled={frozen || !draft.trim()}>
          Queue
        </Button>
      </form>
      <p className="chat-wb-note">
        {met} met · {ticket.criteria_count} of {feature.max_criteria} on the ticket
        {proposed.length ? ` · ${proposed.length} waiting on you` : ""}
      </p>
    </WorkbenchCard>
  );
}

function CriterionRow({
  criterion,
  busy,
  atCap,
  onDecide,
}: {
  criterion: FeatureCriterion;
  busy: boolean;
  /** Accept is certain to be refused; Drop never is, and it is the way out. */
  atCap: boolean;
  onDecide: (ids: string[], accept: boolean) => void;
}) {
  return (
    <li className={`chat-wb-criterion chat-wb-criterion--${criterion.status}`}>
      <span className="chat-wb-criterion-text">{criterion.text}</span>
      {criterion.status === "proposed" ? (
        <span className="chat-wb-criterion-actions">
          <Button
            variant="secondary"
            compact
            disabled={busy || atCap}
            aria-label={`Accept: ${criterion.text}`}
            onClick={() => onDecide([criterion.id], true)}
          >
            Accept
          </Button>
          <Button
            variant="plain"
            compact
            disabled={busy}
            aria-label={`Drop: ${criterion.text}`}
            onClick={() => onDecide([criterion.id], false)}
          >
            Drop
          </Button>
        </span>
      ) : null}
    </li>
  );
}
