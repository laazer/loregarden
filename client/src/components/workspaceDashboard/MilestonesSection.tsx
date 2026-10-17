import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import type { TicketTreeNode } from "../../api/types";
import { ticketPath } from "../../lib/appNavigation";
import { collectMilestones, FINISHED, milestoneProgress } from "../../lib/workspaceDashboard";
import { Button } from "../ui/Button";
import { DashboardSection } from "./DashboardSection";

interface MilestonesSectionProps {
  /** The workspace's whole ticket tree; milestones are found in it at any depth. */
  query: UseQueryResult<TicketTreeNode[]>;
}

/**
 * "How far along is each milestone, and which ones are stuck?"
 *
 * Open milestones only by default: finished ones answer nothing a person
 * catching up needs, and are one click away. Stuck ones sort first.
 */
export function MilestonesSection({ query }: MilestonesSectionProps) {
  const [showFinished, setShowFinished] = useState(false);
  const milestones = collectMilestones(query.data ?? []);
  const open = milestones.filter((node) => !FINISHED.has(node.state));
  const finishedCount = milestones.length - open.length;
  const shown = (showFinished ? milestones : open)
    .map((node) => ({ node, progress: milestoneProgress(node) }))
    .sort((a, b) => b.progress.blocked - a.progress.blocked || a.node.external_id.localeCompare(b.node.external_id));

  return (
    <DashboardSection
      id="wsd-milestones"
      title="Milestones"
      count={query.data ? `${open.length} open` : undefined}
      queries={[query]}
      errorTitle="Could not load milestones"
      empty={milestones.length === 0}
      emptyMessage="No milestones in this workspace. Group related tickets under one from the Console."
      actions={
        finishedCount > 0 ? (
          <Button
            variant="plain"
            className="wsd-chip"
            aria-pressed={showFinished}
            onClick={() => setShowFinished((v) => !v)}
          >
            Show {finishedCount} finished
          </Button>
        ) : null
      }
    >
      {shown.length === 0 ? (
        <p className="wsd-section-empty">Every milestone is finished.</p>
      ) : (
        <ul className="wsd-rows">
          {shown.map(({ node, progress }) => {
            const percent = progress.total ? Math.round((progress.finished / progress.total) * 100) : 0;
            return (
              <li key={node.id} className="wsd-row wsd-row--stack">
                <div className="wsd-row-line">
                  <Link to={ticketPath(node.id)} className="wsd-row-link">
                    <span className="wsd-row-title">{node.title}</span>
                  </Link>
                  <span className="wsd-row-aside wsd-mono">
                    {progress.total === 0 ? "empty" : `${progress.finished}/${progress.total}`}
                  </span>
                </div>
                <div
                  className="wsd-progress"
                  data-blocked={progress.blocked > 0 || undefined}
                  role="progressbar"
                  aria-label={`${node.external_id}: ${progress.finished} of ${progress.total} done`}
                  aria-valuemin={0}
                  aria-valuemax={progress.total}
                  aria-valuenow={progress.finished}
                >
                  <span className="wsd-progress-fill" style={{ transform: `scaleX(${percent / 100})` }} />
                </div>
                {progress.inProgress || progress.blocked ? (
                  <span className="wsd-row-note">
                    {progress.inProgress ? <span>{progress.inProgress} in progress</span> : null}
                    {progress.blocked ? <span className="wsd-danger">{progress.blocked} blocked</span> : null}
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </DashboardSection>
  );
}
