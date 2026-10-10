import { useState } from "react";

import type { InitiativePlan, ScheduleProposal } from "../../../api/initiativeApi";
import { proposalRows } from "../../../lib/scheduleFormat";
import { Button } from "../../ui/Button";
import { ProposalTimeline } from "./ProposalTimeline";

type ProposalView = "timeline" | "changes";

const VIEWS: { view: ProposalView; label: string }[] = [
  { view: "timeline", label: "Timeline" },
  { view: "changes", label: "Changes only" },
];

/**
 * The planner's pending proposal. Nothing it suggests is in the plan until
 * someone presses Accept — this is that moment, so it shows exactly what moves.
 *
 * It opens on the timeline — the whole plan as accepting would leave it —
 * because a draft is judged by its shape; the changed cells alone are a click away.
 */
export function ProposalReview({
  plan,
  proposal,
  busy,
  onAccept,
  onDiscard,
}: {
  plan: InitiativePlan;
  proposal: ScheduleProposal;
  busy: boolean;
  onAccept: () => void;
  onDiscard: () => void;
}) {
  const [view, setView] = useState<ProposalView>("timeline");
  const rows = proposalRows(plan, proposal);
  return (
    <section className="plan-proposal" aria-labelledby="plan-proposal-title">
      <header className="plan-proposal-head">
        <div>
          <h2 id="plan-proposal-title" className="plan-section-title">
            {proposal.source === "draft" ? "Drafted schedule" : "Proposed change"} — waiting for you
          </h2>
          {proposal.rationale ? <p className="plan-proposal-rationale">{proposal.rationale}</p> : null}
        </div>
        <div className="plan-proposal-actions">
          <Button variant="secondary" compact disabled={busy} onClick={onDiscard}>
            Discard
          </Button>
          <Button variant="primary" compact disabled={busy} onClick={onAccept}>
            {busy ? "Saving…" : "Accept"}
          </Button>
        </div>
      </header>
      <div className="plan-mode-buttons" role="group" aria-label="Proposal view">
        {VIEWS.map(({ view: key, label }) => (
          <Button
            key={key}
            variant="plain"
            className={`plan-mode-btn${view === key ? " active" : ""}`}
            aria-pressed={view === key}
            onClick={() => setView(key)}
          >
            {label}
          </Button>
        ))}
      </div>
      {view === "timeline" ? (
        <ProposalTimeline plan={plan} proposal={proposal} />
      ) : rows.length === 0 ? (
        <p className="plan-muted">It matches the current plan — accepting changes nothing but the notes.</p>
      ) : (
        <table className="plan-table">
          <thead>
            <tr>
              <th scope="col">What</th>
              <th scope="col">Now</th>
              <th scope="col">Proposed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.label}</th>
                <td>{row.from}</td>
                <td>{row.to}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
