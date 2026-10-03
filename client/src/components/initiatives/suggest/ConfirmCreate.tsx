import { useEffect, useRef } from "react";

import { Button } from "../../ui/Button";
import type { PlannedInitiative } from "./suggestionEdit";

interface ConfirmCreateProps {
  planned: PlannedInitiative[];
  creating: boolean;
  onConfirm: () => void;
  onBack: () => void;
}

/**
 * The last look before anything is written: every initiative, what it takes,
 * and what moves. Moving features out of their milestones is the part that is
 * slow to undo, so it is said here rather than discovered afterwards.
 */
export function ConfirmCreate({ planned, creating, onConfirm, onBack }: ConfirmCreateProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const leaving = planned.reduce((n, p) => n + p.leaving, 0);
  const emptied = planned.flatMap((p) => p.empties);

  return (
    <section
      className="initiative-card initiative-suggest-confirm"
      aria-labelledby="suggest-confirm-heading"
      onKeyDown={(e) => {
        if (e.key === "Escape" && !creating) onBack();
      }}
    >
      <h2 id="suggest-confirm-heading" ref={heading} tabIndex={-1} className="initiative-section-title">
        Create {planned.length} {planned.length === 1 ? "initiative" : "initiatives"}?
      </h2>
      <ul className="initiative-suggest-confirm-list">
        {planned.map((p) => (
          <li key={`${p.kind}:${p.title}`}>
            <strong>{p.title}</strong> — {p.item_ids.length} {p.kind === "sprint" ? "items" : "milestones"}
            {p.target_date ? `, due ${p.target_date}` : ""}
          </li>
        ))}
      </ul>
      {leaving > 0 && (
        <p className="initiative-hint initiative-warn">
          {leaving} features and bugs move out of their milestones into the sprint. To put one back later, use “Move to
          milestone” on its row on the Initiatives page.
        </p>
      )}
      {emptied.length > 0 && (
        <p className="initiative-hint initiative-warn">
          {emptied.join(", ")} will have no open work left and roll up as done.
        </p>
      )}
      <div className="initiative-create-actions">
        <Button variant="secondary" disabled={creating} onClick={onBack}>
          Back to editing
        </Button>
        <Button variant="primary" disabled={creating} aria-busy={creating} onClick={onConfirm}>
          {creating ? "Creating…" : "Create"}
        </Button>
      </div>
    </section>
  );
}
