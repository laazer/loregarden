import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import type { ApprovalHistoryItem } from "../../api/chatTypes";
import { api } from "../../api/client";
import { formatLocalTimestamp, formatRelativeAge } from "../../lib/timestamps";
import { navigateToTicketTab } from "../../lib/useAppNavigation";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";

/** Rows shown before "Show all": enough for a typical ticket (most have under 10). */
const VISIBLE_ROWS = 8;

/** The same question answered the same way, on the same ticket — one row with a count. */
interface DecisionGroup {
  latest: ApprovalHistoryItem;
  count: number;
}

/**
 * Agents ask the same permission over and over ("Allow Bash" twenty times on one
 * ticket). Listed raw, the one gate decision that mattered drowns in them.
 * Newest-first order is kept, by each group's latest answer.
 */
function groupDecisions(items: ApprovalHistoryItem[]): DecisionGroup[] {
  const groups = new Map<string, DecisionGroup>();
  for (const item of items) {
    const key = `${item.ticket_id}\u0000${item.title}\u0000${item.status}\u0000${item.resolved_by}`;
    const group = groups.get(key);
    if (group) group.count += 1;
    else groups.set(key, { latest: item, count: 1 });
  }
  return [...groups.values()];
}

function outcomeLabel(item: ApprovalHistoryItem): string {
  if (item.status === "rejected") return "Rejected";
  return item.resolved_by === "automation" ? "Auto-approved" : "Approved";
}

/**
 * What has already been decided on this ticket: the operator's way to answer
 * "did I sign this off, and when?" without digging through the Timeline.
 */
export function ApprovalHistoryList({ ticketId }: { ticketId: string }) {
  const [showAll, setShowAll] = useState(false);
  const history = useQuery({
    queryKey: ["approval-history", ticketId],
    queryFn: () => api.approvalHistory(ticketId),
  });

  if (history.isLoading) {
    return <div className="approvals-view-note">Loading past decisions…</div>;
  }
  if (history.isError) {
    return (
      <div className="approvals-view-error" role="alert">
        Could not load past decisions: {describeError(history.error, "the request failed")}.{" "}
        <Button variant="plain" className="approval-inline-toggle" onClick={() => void history.refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  const groups = groupDecisions(history.data ?? []);
  if (!groups.length) {
    return <div className="approvals-view-note">No approvals have been decided on this ticket yet.</div>;
  }
  const visible = showAll ? groups : groups.slice(0, VISIBLE_ROWS);

  return (
    <>
      <ul className="approval-history">
        {visible.map(({ latest, count }) => (
          <li key={latest.id}>
            <Button
              variant="plain"
              className="approval-history-row"
              title={`Open ${latest.ticket_external_id || "this ticket"}'s Timeline`}
              onClick={() => navigateToTicketTab(latest.ticket_id, "timeline")}
            >
              <span className={`approval-history-outcome approval-history-outcome--${latest.status}`}>
                {outcomeLabel(latest)}
              </span>
              <span className="approval-history-title">{latest.title}</span>
              {count > 1 ? <span className="approval-history-meta">×{count}</span> : null}
              {latest.ticket_id !== ticketId && latest.ticket_external_id ? (
                <span className="approval-history-meta approval-history-scope">{latest.ticket_external_id}</span>
              ) : null}
              <span className="approval-history-when" title={formatLocalTimestamp(latest.resolved_at)}>
                {latest.stage_name ? `${latest.stage_name} · ` : ""}
                {formatRelativeAge(latest.resolved_at) || "—"}
              </span>
            </Button>
          </li>
        ))}
      </ul>
      {groups.length > VISIBLE_ROWS ? (
        <Button
          variant="plain"
          className="approval-inline-toggle"
          aria-expanded={showAll}
          onClick={() => setShowAll(!showAll)}
        >
          {showAll ? "Show fewer" : `Show all ${groups.length}`}
        </Button>
      ) : null}
    </>
  );
}
