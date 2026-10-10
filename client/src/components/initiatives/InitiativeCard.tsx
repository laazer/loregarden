import { useState } from "react";

import type { InitiativeMilestone, InitiativeView } from "../../api/client";
import { MarkdownContent } from "../chat/MarkdownContent";
import { navigateToInitiative, navigateToTicket } from "../../lib/useAppNavigation";
import { Button } from "../ui/Button";
import { AddSprintWork, MoveToMilestone } from "./SprintWorkControls";
import { TICKET_STATE_COLORS, TICKET_STATE_LABELS } from "../../lib/ticketStates";
import { progressFillStyle } from "../../lib/progressFill";

interface InitiativeCardProps {
  initiative: InitiativeView;
  attachable: InitiativeMilestone[];
  /** Every milestone a sprint's feature or bug could move back to, any workspace. */
  milestoneChoices: InitiativeMilestone[];
  /** Milestone or initiative id whose write is in flight; its controls disable. */
  busyId: string | null;
  onAttach: (milestoneId: string) => void;
  onDetach: (milestoneId: string) => void;
  /** Re-parent a feature or bug: into this initiative, or out to a milestone. */
  onMoveWork: (ticketId: string, parentId: string) => void;
  onDelete: () => void;
}

function StatePill({ state }: { state: InitiativeView["state"] }) {
  return (
    <span className="initiative-state" style={{ color: TICKET_STATE_COLORS[state] }}>
      {TICKET_STATE_LABELS[state]}
    </span>
  );
}

/** Where a row lives; a member also names the milestone it stays under. */
function placeLabel(m: InitiativeMilestone): string {
  const kind = m.work_item_type === "milestone" && !m.member ? "" : `${m.work_item_type} · `;
  const home = m.home_milestone ? `in ${m.home_milestone} · ` : "";
  return `${kind}${home}${m.workspace_slug}`;
}

function milestoneOptionLabel(m: InitiativeMilestone): string {
  return `${m.workspace_slug} · ${m.external_id} — ${m.title}`;
}

function AttachControl({
  initiative,
  attachable,
  disabled,
  onAttach,
}: {
  initiative: InitiativeView;
  attachable: InitiativeMilestone[];
  disabled: boolean;
  onAttach: (milestoneId: string) => void;
}) {
  const [choice, setChoice] = useState("");
  if (attachable.length === 0) {
    return (
      <p className="initiative-hint">
        Every milestone already belongs to an initiative. Create a milestone in any workspace to
        attach it here.
      </p>
    );
  }
  return (
    <div className="initiative-attach">
      <select
        className="btn-secondary filter-select"
        aria-label={`Milestone to attach to ${initiative.title}`}
        value={choice}
        disabled={disabled}
        onChange={(e) => setChoice(e.target.value)}
      >
        <option value="">Attach a milestone…</option>
        {attachable.map((m) => (
          <option key={m.id} value={m.id}>
            {milestoneOptionLabel(m)}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn-secondary"
        disabled={disabled || !choice}
        onClick={() => {
          onAttach(choice);
          setChoice("");
        }}
      >
        Attach
      </button>
    </div>
  );
}

function MilestoneRow({
  milestone,
  milestoneChoices,
  busy,
  onDetach,
  onMoveTo,
}: {
  milestone: InitiativeMilestone;
  milestoneChoices: InitiativeMilestone[];
  busy: boolean;
  onDetach: () => void;
  onMoveTo: (milestoneId: string) => void;
}) {
  return (
    <li className="initiative-milestone">
      <span
        className="tree-state-dot"
        style={{ background: TICKET_STATE_COLORS[milestone.state] }}
        aria-hidden
      />
      <button
        type="button"
        className="initiative-milestone-link"
        title={`Open ${milestone.external_id}`}
        onClick={() => navigateToTicket(milestone.id)}
      >
        <span className="initiative-mono">{milestone.external_id}</span> {milestone.title}
      </button>
      <span className="initiative-ws-pill">{placeLabel(milestone)}</span>
      <span className="initiative-milestone-state">{TICKET_STATE_LABELS[milestone.state]}</span>
      {milestone.member ? (
        <span className="initiative-muted" title="Tracked without moving it: remove it from Plan & board">
          Tracked
        </span>
      ) : milestone.work_item_type === "milestone" ? (
        <Button
          variant="secondary"
          className="initiative-small-btn"
          aria-label={`Detach ${milestone.title} from this initiative`}
          disabled={busy}
          onClick={onDetach}
        >
          {busy ? "Detaching…" : "Detach"}
        </Button>
      ) : (
        <MoveToMilestone item={milestone} milestones={milestoneChoices} busy={busy} onMove={onMoveTo} />
      )}
    </li>
  );
}

export function InitiativeCard({
  initiative,
  attachable,
  milestoneChoices,
  busyId,
  onAttach,
  onDetach,
  onMoveWork,
  onDelete,
}: InitiativeCardProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { resolved, total } = initiative.progress;
  const pct = total === 0 ? 0 : Math.round((resolved / total) * 100);
  const deleting = busyId === initiative.id;
  const hasMilestones = initiative.milestones.length > 0;

  return (
    <article className="initiative-card" aria-labelledby={`initiative-${initiative.id}`}>
      <header className="initiative-card-head">
        <div>
          <div className="initiative-mono initiative-muted">{initiative.external_id}</div>
          <h2 id={`initiative-${initiative.id}`} className="initiative-title">
            {initiative.title}
          </h2>
        </div>
        <div className="initiative-card-head-actions">
          <StatePill state={initiative.state} />
          <Button variant="primary" compact onClick={() => navigateToInitiative(initiative.external_id || initiative.id)}>
            Plan &amp; board
          </Button>
        </div>
      </header>
      {initiative.description && (
        <MarkdownContent
          content={initiative.description}
          className="initiative-desc"
          readerTitle={initiative.title}
          readerSubtitle={initiative.external_id}
        />
      )}

      <div
        className="initiative-progress"
        role="progressbar"
        aria-label={`${initiative.title} progress`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div className="initiative-progress-fill" style={progressFillStyle(pct)} />
      </div>
      <div className="initiative-muted">
        {total === 0
          ? "No milestones yet"
          : `${resolved} of ${total} ${
              initiative.milestones.every((m) => m.work_item_type === "milestone") ? "milestones" : "items"
            } resolved · ${initiative.workspaces.join(", ")}`}
      </div>

      {hasMilestones ? (
        <ul className="initiative-milestones">
          {initiative.milestones.map((m) => (
            <MilestoneRow
              key={m.id}
              milestone={m}
              milestoneChoices={milestoneChoices}
              busy={busyId === m.id}
              onDetach={() => onDetach(m.id)}
              onMoveTo={(milestoneId) => onMoveWork(m.id, milestoneId)}
            />
          ))}
        </ul>
      ) : (
        <p className="initiative-hint">
          Attach a milestone below. Its progress, and every milestone after it, rolls up here.
        </p>
      )}

      {initiative.milestones.some((m) => !m.member && m.work_item_type !== "milestone") && (
        <AddSprintWork
          initiative={initiative}
          disabled={busyId !== null}
          onAdd={(ticketId) => onMoveWork(ticketId, initiative.id)}
        />
      )}

      <AttachControl
        initiative={initiative}
        attachable={attachable}
        disabled={busyId !== null}
        onAttach={onAttach}
      />

      <footer className="initiative-card-foot">
        {confirmDelete ? (
          <>
            <span className="initiative-muted">Delete this initiative?</span>
            <button
              type="button"
              className="btn-secondary"
              disabled={deleting}
              onClick={() => setConfirmDelete(false)}
            >
              Keep
            </button>
            <button type="button" className="btn-primary initiative-danger-btn" disabled={deleting} onClick={onDelete}>
              {deleting ? "Deleting…" : "Delete"}
            </button>
          </>
        ) : (
          <Button
            variant="secondary"
            className="initiative-small-btn"
            disabled={hasMilestones || busyId !== null}
            title={
              hasMilestones
                ? "Detach its milestones and remove its tracked tickets before deleting an initiative"
                : undefined
            }
            onClick={() => setConfirmDelete(true)}
          >
            Delete initiative
          </Button>
        )}
      </footer>
    </article>
  );
}
