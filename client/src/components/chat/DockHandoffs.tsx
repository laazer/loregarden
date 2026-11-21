import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { useChatScopeKey } from "../../hooks/useChatScope";
import { useDebounced } from "../../hooks/useDebounced";
import { api, type HandoffEntry, type HandoffItemStatus } from "../../api/client";
import { ticketPath } from "../../lib/appNavigation";
import { chatScopeWorkspaceSlug } from "../../lib/chatScope";
import type { StatusTone } from "../../lib/statusTone";
import { formatLocalTimestamp, formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { StatusChip } from "../ui/StatusChip";

import "./DockHandoffs.css";

/** Past this many handoffs in scope the list gets a filter; a ticket's chain rarely reaches it. */
const FILTER_FROM = 12;

/** How long typing must pause before the search goes to the server. */
const SEARCH_DELAY_MS = 250;

const ITEM_TONE: Record<HandoffItemStatus, StatusTone> = {
  complete: "done",
  incomplete: "bad",
  deferred: "wait",
  blocked: "bad",
};

const ITEM_LABEL: Record<HandoffItemStatus, string> = {
  complete: "Complete",
  incomplete: "Incomplete",
  deferred: "Deferred",
  blocked: "Blocked",
};

/**
 * "What did each agent hand the next, and did it meet its checklist?" — the
 * handoffs between stages, newest first. On a ticket, that ticket's chain; on a
 * workspace thread, every ticket's in the workspace it answers for (the chat's
 * scope, not the page's filter: on "All workspaces" the thread still names one);
 * on a global chat, every workspace's. Each row leads
 * to its ticket, and opens to the checklist and evidence that went with it.
 */
export function DockHandoffs({ ticketId }: { ticketId: string | null }) {
  const workspaceSlug = chatScopeWorkspaceSlug(useChatScopeKey());
  const [filter, setFilter] = useState("");
  const search = useDebounced(filter.trim(), SEARCH_DELAY_MS);
  const scopeKey = ["handoffs", ticketId ?? "", ticketId ? "" : workspaceSlug] as const;
  const feed = useQuery({
    queryKey: [...scopeKey, search],
    queryFn: () => api.handoffs(ticketId ? { ticketId, q: search } : { workspace: workspaceSlug, q: search }),
    // While a new search is on the wire, keep the last result of the same scope
    // up rather than flashing the skeleton on every keystroke. Another scope's
    // rows (the ticket before this one) are never shown in its place.
    placeholderData: (previous, previousQuery) =>
      previousQuery && scopeKey.every((part, index) => previousQuery.queryKey[index] === part) ? previous : undefined,
  });
  const filterId = useId();

  if (feed.isPending && !filter) {
    return <PaneSkeleton variant="list" rows={6} label="Loading handoffs…" />;
  }
  const data = feed.data;
  // Shown whenever there is something to narrow, and kept while a search is
  // typed: a search that narrows below the threshold must not take its own box away.
  const showFilter = filter !== "" || search !== "" || (data !== undefined && data.total > FILTER_FROM);
  // Typed but not yet sent, or sent and not yet back.
  const searching = feed.isPlaceholderData || search !== filter.trim();
  return (
    <div className="dock-handoffs">
      {showFilter || data ? (
        <div className="dock-handoffs-bar">
          {data ? (
            <span className="dock-handoffs-count" aria-live="polite">
              {searching ? "Searching…" : countLabel(data.items.length, data.total, search)}
            </span>
          ) : null}
          {showFilter ? (
            <>
              <label htmlFor={filterId} className="visually-hidden">
                Filter handoffs by agent or ticket
              </label>
              <Input
                id={filterId}
                type="search"
                className="dock-handoffs-filter"
                placeholder="Filter by agent or ticket"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
            </>
          ) : null}
        </div>
      ) : null}
      {feed.isError ? (
        <div className="dock-handoffs-error" role="alert">
          <p>
            Couldn't {search ? `search handoffs for “${search}”` : "load handoffs"}:{" "}
            {describeError(feed.error, "the request failed")}.
          </p>
          <Button variant="plain" onClick={() => void feed.refetch()} disabled={feed.isFetching}>
            {feed.isFetching ? "Retrying…" : "Try again"}
          </Button>
        </div>
      ) : !data ? (
        <PaneSkeleton variant="list" rows={6} label="Searching handoffs…" />
      ) : data.items.length === 0 ? (
        <p className="dock-handoffs-empty">
          {search
            ? `No handoff ${ticketId ? "on this ticket" : workspaceSlug ? `in ${workspaceSlug}` : "anywhere"} matches “${search}”.`
            : ticketId
              ? "No handoffs on this ticket yet. When a stage's agent finishes and passes the work on, what it handed over appears here."
              : "No handoffs here yet. When a stage's agent finishes and passes the work on, what it handed over appears here, newest first."}
        </p>
      ) : (
        <ol
          className="dock-handoffs-list"
          aria-label="Handoffs between agents, newest first"
          aria-busy={searching}
        >
          {data.items.map((entry) => (
            <HandoffRow key={entry.id} entry={entry} showTicket={!ticketId} />
          ))}
        </ol>
      )}
    </div>
  );
}

/** "12 handoffs", "Newest 50 of 1,129", and the same with "matching" when searched. */
function countLabel(shown: number, total: number, search: string): string {
  const matching = search ? " matching" : "";
  if (total > shown) return `Newest ${shown} of ${total.toLocaleString()}${matching}`;
  return `${total.toLocaleString()} ${total === 1 ? "handoff" : "handoffs"}${matching}`;
}

function HandoffRow({ entry, showTicket }: { entry: HandoffEntry; showTicket: boolean }) {
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const short =
    entry.required_items_met !== null &&
    entry.total_required_items !== null &&
    entry.required_items_met < entry.total_required_items;
  const pair = entry.unreadable ? "Unreadable handoff" : `${entry.from_agent} → ${entry.to_agent}`;
  return (
    <li className="dock-handoff">
      <div className="dock-handoff-head">
        <span className="dock-handoff-pair">{pair}</span>
        <time className="dock-handoff-time" dateTime={entry.created_at} title={formatLocalTimestamp(entry.created_at)}>
          {formatRelativeAge(entry.created_at)}
        </time>
      </div>
      {showTicket ? (
        <Link className="dock-handoff-ticket" to={ticketPath(entry.ticket_id)} title={entry.ticket_title}>
          <span className="dock-handoff-ticket-id">{entry.ticket_external_id}</span> {entry.ticket_title}
        </Link>
      ) : null}
      <div className="dock-handoff-meta">
        {entry.total_required_items !== null && entry.required_items_met !== null ? (
          <StatusChip
            tone={short ? "wait" : "done"}
            label={`${entry.required_items_met}/${entry.total_required_items} required met`}
          />
        ) : null}
        {entry.checklist.length > 0 || entry.note || entry.unreadable ? (
          <Button
            variant="plain"
            className="dock-handoff-toggle"
            aria-expanded={open}
            aria-controls={detailId}
            onClick={() => setOpen((was) => !was)}
          >
            {open ? "Hide" : "Show"} {entry.checklist.length > 0 ? `checklist (${entry.checklist.length})` : "details"}
          </Button>
        ) : null}
      </div>
      {open ? (
        <div id={detailId} className="dock-handoff-detail">
          {entry.unreadable ? <p className="dock-handoff-unreadable">{entry.unreadable}</p> : null}
          {entry.note ? <p className="dock-handoff-note">{entry.note}</p> : null}
          {entry.checklist.length > 0 ? (
            <ul className="dock-handoff-items">
              {entry.checklist.map((item, index) => (
                <li key={item.item_key || index}>
                  <div className="dock-handoff-item-head">
                    <StatusChip tone={ITEM_TONE[item.status]} label={ITEM_LABEL[item.status]} />
                    <span className="dock-handoff-item-name">
                      {item.item || item.item_key}
                      {item.required ? "" : " (optional)"}
                    </span>
                    {item.certainty === "inferred" ? (
                      <span className="dock-handoff-item-certainty" title="No evidence artifact backs this claim">
                        unverified
                      </span>
                    ) : null}
                  </div>
                  {item.evidence ? <p className="dock-handoff-item-evidence">{item.evidence}</p> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
