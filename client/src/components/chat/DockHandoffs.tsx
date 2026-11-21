import { useId, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { useChatScopeKey } from "../../hooks/useChatScope";
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

/** Past this many rows the list gets a filter; a ticket's chain rarely reaches it. */
const FILTER_FROM = 12;

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
  const scope = ticketId ? { ticketId } : { workspace: workspaceSlug };
  const feed = useQuery({
    queryKey: ["handoffs", ticketId ?? "", ticketId ? "" : workspaceSlug],
    queryFn: () => api.handoffs(scope),
  });
  const [filter, setFilter] = useState("");
  const filterId = useId();
  const items = feed.data?.items;
  const shown = useMemo(() => matching(items ?? [], filter), [items, filter]);

  if (feed.isPending) {
    return <PaneSkeleton variant="list" rows={6} label="Loading handoffs…" />;
  }
  if (feed.isError) {
    return (
      <div className="dock-handoffs-error" role="alert">
        <p>Couldn't load handoffs: {describeError(feed.error, "the request failed")}.</p>
        <Button variant="plain" onClick={() => void feed.refetch()} disabled={feed.isFetching}>
          {feed.isFetching ? "Retrying…" : "Try again"}
        </Button>
      </div>
    );
  }
  const { total } = feed.data;
  if (feed.data.items.length === 0) {
    return (
      <p className="dock-handoffs-empty">
        {ticketId
          ? "No handoffs on this ticket yet. When a stage's agent finishes and passes the work on, what it handed over appears here."
          : "No handoffs here yet. When a stage's agent finishes and passes the work on, what it handed over appears here, newest first."}
      </p>
    );
  }
  return (
    <div className="dock-handoffs">
      <div className="dock-handoffs-bar">
        <span className="dock-handoffs-count">
          {total > feed.data.items.length
            ? `Newest ${feed.data.items.length} of ${total.toLocaleString()}`
            : `${total} ${total === 1 ? "handoff" : "handoffs"}`}
        </span>
        {feed.data.items.length > FILTER_FROM ? (
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
      {shown.length === 0 ? (
        <p className="dock-handoffs-empty">No handoff matches “{filter}”.</p>
      ) : (
        <ol className="dock-handoffs-list" aria-label="Handoffs between agents, newest first">
          {shown.map((entry) => (
            <HandoffRow key={entry.id} entry={entry} showTicket={!ticketId} />
          ))}
        </ol>
      )}
    </div>
  );
}

function matching(items: readonly HandoffEntry[], filter: string): readonly HandoffEntry[] {
  const needle = filter.trim().toLowerCase();
  if (!needle) return items;
  return items.filter((entry) =>
    [entry.from_agent, entry.to_agent, entry.ticket_external_id, entry.ticket_title].some((field) =>
      field.toLowerCase().includes(needle),
    ),
  );
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
