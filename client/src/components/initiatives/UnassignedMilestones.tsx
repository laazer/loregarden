import { useMemo, useState } from "react";

import type { InitiativeMilestone } from "../../api/client";
import { navigateToTicket } from "../../lib/useAppNavigation";
import { TICKET_STATE_COLORS, TICKET_STATE_LABELS } from "../../lib/ticketStates";

const RESOLVED = new Set(["done", "wont_do"]);

interface UnassignedMilestonesProps {
  milestones: InitiativeMilestone[];
  selected: Set<string>;
  onToggle: (milestoneId: string) => void;
  onGroup: () => void;
  disabled: boolean;
}

/**
 * Every milestone no initiative owns, across every workspace, with a way to
 * group some of them into one.
 *
 * With zero initiatives the page used to be one centred sentence — nothing on
 * it said what an initiative would contain, or that 74 milestones across four
 * workspaces were already sitting there waiting for one. This is the material
 * an initiative is made of, so it is shown whether or not one exists yet.
 */
export function UnassignedMilestones({ milestones, selected, onToggle, onGroup, disabled }: UnassignedMilestonesProps) {
  const [showResolved, setShowResolved] = useState(false);
  const resolvedCount = milestones.filter((m) => RESOLVED.has(m.state)).length;

  const byWorkspace = useMemo(() => {
    const groups = new Map<string, InitiativeMilestone[]>();
    for (const milestone of milestones) {
      if (!showResolved && RESOLVED.has(milestone.state)) continue;
      groups.set(milestone.workspace_slug, [...(groups.get(milestone.workspace_slug) ?? []), milestone]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [milestones, showResolved]);

  return (
    <section className="initiative-unassigned" aria-labelledby="initiative-unassigned-title">
      <header className="initiative-unassigned-head">
        <div>
          <h2 id="initiative-unassigned-title" className="initiative-section-title">
            Milestones without an initiative <span className="initiative-mono initiative-muted">{milestones.length}</span>
          </h2>
          <p className="initiative-hint">
            Tick the milestones that serve one goal, then group them — the initiative rolls up their progress across
            workspaces.
          </p>
        </div>
        <div className="initiative-unassigned-actions">
          {resolvedCount > 0 && (
            <label className="initiative-toggle">
              <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} />
              Show finished ({resolvedCount})
            </label>
          )}
          <button type="button" className="btn-primary" disabled={disabled || selected.size === 0} onClick={onGroup}>
            {selected.size === 0 ? "Group into initiative" : `Group ${selected.size} into initiative`}
          </button>
        </div>
      </header>

      {byWorkspace.length === 0 ? (
        <p className="initiative-hint">
          {milestones.length === 0
            ? "Every milestone already belongs to an initiative. New milestones appear here until one claims them."
            : "Every milestone without an initiative is finished. Tick “Show finished” to see them."}
        </p>
      ) : (
        <div className="initiative-unassigned-grid">
          {byWorkspace.map(([workspace, rows]) => (
            <div key={workspace} className="initiative-unassigned-ws">
              <h3 className="initiative-ws-title">{workspace}</h3>
              <ul className="initiative-pick-list">
                {rows.map((milestone) => (
                  <li key={milestone.id} className="initiative-pick">
                    <input
                      type="checkbox"
                      id={`pick-${milestone.id}`}
                      checked={selected.has(milestone.id)}
                      disabled={disabled}
                      onChange={() => onToggle(milestone.id)}
                    />
                    <label htmlFor={`pick-${milestone.id}`} className="initiative-pick-label">
                      <span
                        className="tree-state-dot"
                        style={{ background: TICKET_STATE_COLORS[milestone.state] }}
                        aria-hidden
                      />
                      <span className="initiative-pick-title">{milestone.title}</span>
                      <span className="initiative-milestone-state">{TICKET_STATE_LABELS[milestone.state]}</span>
                    </label>
                    <button
                      type="button"
                      className="initiative-open-btn"
                      aria-label={`Open ${milestone.external_id}`}
                      title={`Open ${milestone.external_id}`}
                      onClick={() => navigateToTicket(milestone.id)}
                    >
                      ↗
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
