import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ticketPath } from "../lib/appNavigation";
import {
  groupNoticesByReason,
  reasonLabel,
  type BranchRemovalNotice,
} from "../lib/branchRemovalNotices";
import { Button } from "./ui/Button";

/**
 * How many refusal rows a bulk run shows before the rest go behind a
 * disclosure.
 *
 * Measured on this workspace on 2026-10-16: 41 active worktrees, 13 of them
 * (32%) owned by a ticket with no `landed_sha`, so one bulk pass can refuse in
 * the double digits. Ten rows is enough to read the shape of a refusal without
 * the block becoming the screen.
 */
const EXPANDED_ROW_LIMIT = 10;

/** The copy for a refusal with no ticket to open: the two verbs, named. */
const WRITE_A_RECEIPT =
  "Land the branch or publish the tree — those are the two things that write the " +
  "receipt this delete needs.";

/**
 * The row's own ending: a control where there is a ticket to open, the two
 * verbs where there is not — except cleanup_failed, which is not a receipt
 * miss, so the row ends at notice.detail.
 */
export function BranchNoticeRow({ notice }: { notice: BranchRemovalNotice }) {
  const navigate = useNavigate();
  const ticket = notice.ticket;
  return (
    <li className="branch-cleanup-refusal">
      <div className="branch-cleanup-refusal-head">
        <code>{notice.label}</code>
        {notice.parkedPath ? (
          <>
            <span className="branch-cleanup-refusal-parked-label">parked at</span>
            <code>{notice.parkedPath}</code>
          </>
        ) : null}
      </div>
      {notice.detail ? (
        <span className="branch-cleanup-refusal-detail">{notice.detail}</span>
      ) : null}
      {notice.reason === "cleanup_failed" ? null : ticket ? (
        <Button variant="secondary" compact onClick={() => navigate(ticketPath(ticket.id))}>
          Open {ticket.external_id}
        </Button>
      ) : (
        <span className="branch-cleanup-refusal-detail">{WRITE_A_RECEIPT}</span>
      )}
    </li>
  );
}

/**
 * The amber block: a refused outcome, not a transport error.
 *
 * Deliberately not the red `.branch-triage-delete-error` block a transport
 * failure uses. A no_receipt refusal means the safety bar worked; a
 * cleanup_failed refusal may have unlinked part of the tree, so the heading
 * drops "nothing was deleted" when any row is that reason. It is announced
 * politely and takes no focus, so a retry needs no re-navigation, and it has
 * no entrance animation: the modal is already on screen and this is
 * information wanted now rather than a thing to watch arrive.
 */
export function BranchRefusalBlock({
  notices,
  pluralNoun,
}: {
  notices: BranchRemovalNotice[];
  /** `branch(es)` or `worktree(s)` — the heading reads `N <noun> refused`. */
  pluralNoun: string;
}) {
  const [expanded, setExpanded] = useState(false);

  // Zero refusals renders nothing at all, not an empty box saying so.
  if (!notices.length) return null;

  const visibleCount = expanded ? notices.length : Math.min(notices.length, EXPANDED_ROW_LIMIT);
  const shown = new Set(notices.slice(0, visibleCount));
  const groups = groupNoticesByReason(notices)
    .map((group) => ({ ...group, visible: group.items.filter((item) => shown.has(item)) }))
    .filter((group) => group.visible.length > 0);
  const heading = notices.some((notice) => notice.reason === "cleanup_failed")
    ? `${notices.length} ${pluralNoun} refused`
    : `${notices.length} ${pluralNoun} refused — nothing was deleted`;

  return (
    <div className="branch-cleanup-refusals" role="status" aria-live="polite">
      <div className="modal-section-title">{heading}</div>
      {groups.map((group) => (
        <div key={group.reason} className="branch-cleanup-refusal-group">
          <div className="branch-cleanup-refusal-reason">
            {reasonLabel(group.reason)}
            <span className="branch-cleanup-count">{group.items.length}</span>
          </div>
          <ul className="branch-cleanup-refusal-list">
            {group.visible.map((notice) => (
              <BranchNoticeRow key={notice.label} notice={notice} />
            ))}
          </ul>
        </div>
      ))}
      {notices.length > EXPANDED_ROW_LIMIT ? (
        <Button
          variant="secondary"
          compact
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? "Show fewer" : `Show all ${notices.length}`}
        </Button>
      ) : null}
    </div>
  );
}
