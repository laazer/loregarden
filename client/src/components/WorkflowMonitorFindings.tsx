import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { formatRelativeAge } from "../lib/timestamps";

interface WorkflowMonitorFindingsProps {
  ticketId: string;
}

/**
 * What the workflow monitor has noticed about this ticket.
 *
 * Report-only, and deliberately quiet: it renders nothing when there is nothing
 * to say. The monitor runs on the reconcile timer against every ticket, so a
 * panel that were always present would be noise on the overwhelming majority of
 * tickets that are fine.
 *
 * The summary carries the numbers — attempts against baseline, failure rate
 * against the workspace — because a reader who has to go and run the query
 * themselves will not. Duration since first_seen is the secondary line;
 * occurrences (reconcile-sweep ticks) stay unread.
 */
export function WorkflowMonitorFindings({ ticketId }: WorkflowMonitorFindingsProps) {
  const { data: findings } = useQuery({
    queryKey: ["monitor-findings", ticketId],
    queryFn: () => api.monitorFindings(ticketId),
    enabled: Boolean(ticketId),
  });

  if (!findings?.length) return null;

  return (
    <div className="monitor-findings" role="status">
      <strong>Workflow monitor</strong>
      <ul>
        {findings.map((finding) => (
          <li key={`${finding.condition}:${finding.stage_key}`}>
            {finding.summary}
            {finding.first_seen && (
              <span className="monitor-findings-count">
                {" "}
                first seen {formatRelativeAge(finding.first_seen)}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
