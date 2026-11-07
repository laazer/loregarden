import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import type { AutoDecision } from "../../api/autoDecisionApi";
import { autoDecisionsKey } from "../../api/autoDecisionApi";
import { api } from "../../api/client";
import { ticketPath } from "../../lib/appNavigation";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";

/** Past this many, the rest are a count: the newest are the ones to review first. */
const SHOWN = 12;

const KIND_LABEL: Record<AutoDecision["decision"], string> = {
  split_criteria: "Criteria split off",
  deferred_undecided_findings: "Findings deferred",
};

/**
 * "What did the pipeline decide under this ticket without asking me, and
 * should I undo any of it?" Each row names the ticket it happened on and,
 * for a split, the ticket the criteria moved to; both are links.
 *
 * `alwaysShow` is for a tree root (a milestone or initiative), where an empty
 * list is itself the answer. Elsewhere the panel appears only when there is
 * something to review.
 */
export function AutoDecisionsPanel({ ticketId, alwaysShow }: { ticketId: string; alwaysShow: boolean }) {
  const decisions = useQuery({
    queryKey: autoDecisionsKey(ticketId),
    queryFn: () => api.autoDecisions(ticketId),
  });

  // Loading renders nothing: one indexed read, well under the wait a skeleton is for.
  if (decisions.isPending) return null;
  if (decisions.isError) {
    return (
      <div className="state-card">
        <div className="state-label">Decided without you</div>
        <p className="auto-decisions-error" role="status">
          Could not load the decisions made under this ticket:{" "}
          {describeError(decisions.error, "the request failed")}. Reopen the ticket to try again.
        </p>
      </div>
    );
  }
  const rows = decisions.data;
  if (rows.length === 0 && !alwaysShow) return null;
  return (
    <div className="state-card">
      <div className="state-label" id="auto-decisions-label">
        Decided without you
      </div>
      {rows.length === 0 ? (
        <p className="auto-decisions-empty">
          Nothing yet. When a ticket under this one splits off criteria it could not meet, or defers
          findings its spec would not decide, it is listed here to review.
        </p>
      ) : (
        <ul className="auto-decisions" aria-labelledby="auto-decisions-label">
          {rows.slice(0, SHOWN).map((row) => (
            <li key={`${row.ticket_id}-${row.at}-${row.decision}`}>
              <span className="count-pill">{KIND_LABEL[row.decision]}</span>{" "}
              <Link to={ticketPath(row.external_id)}>{row.external_id}</Link>
              {row.related_external_id ? (
                <>
                  {" → "}
                  <Link to={ticketPath(row.related_external_id)}>{row.related_external_id}</Link>
                </>
              ) : null}{" "}
              <span className="auto-decisions-age">{formatRelativeAge(row.at)}</span>
              <p className="auto-decisions-reason">{row.reason}</p>
            </li>
          ))}
        </ul>
      )}
      {rows.length > SHOWN ? (
        <p className="auto-decisions-more">
          {rows.length - SHOWN} older decisions are on the history of the tickets they happened on.
        </p>
      ) : null}
    </div>
  );
}
