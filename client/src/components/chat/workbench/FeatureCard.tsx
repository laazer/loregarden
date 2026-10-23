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
 * "What is this feature asked to do, and what is waiting on me?" The loop's
 * criteria by stage, Accept / Drop on each proposal, and one field to add a
 * criterion yourself. Without a loop, the way to start one.
 */
export function FeatureCard({
  feature,
  workspaces,
  defaultWorkspace,
  busy,
  starting,
  onStart,
  onDecide,
  onAdd,
}: {
  feature: FeatureLoop | null;
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
  const { ticket } = feature;

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
      {feature.reason ? (
        <p className="chat-wb-warn" role="status">
          {feature.reason}
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
                    variant="plain"
                    className="chat-side-card-link"
                    disabled={busy}
                    onClick={() => onDecide(rows.map((r) => r.id), true)}
                  >
                    Accept all
                  </Button>
                ) : null}
              </div>
              <ul className="chat-wb-criteria">
                {rows.map((criterion) => (
                  <CriterionRow key={criterion.id} criterion={criterion} busy={busy} onDecide={onDecide} />
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
          if (!text || busy) return;
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
          disabled={busy}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button type="submit" variant="secondary" compact disabled={busy || !draft.trim()}>
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
  onDecide,
}: {
  criterion: FeatureCriterion;
  busy: boolean;
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
            disabled={busy}
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
