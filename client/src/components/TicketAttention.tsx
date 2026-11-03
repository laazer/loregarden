import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../api/client";
import type { TicketDetail } from "../api/client";
import type { Delivery, MonitorFinding } from "../api/types";
import { formatRelativeAge } from "../lib/timestamps";
import { navigateToTicketTab } from "../lib/useAppNavigation";
import { openReader } from "../state/readerStore";
import { blockHeadline } from "../utils/blockKinds";
import { Button } from "./ui/Button";
import { TicketWaitingOn } from "./waiting/TicketWaitingOn";
import "./TicketAttention.css";

/** Past this, the one visible line is a teaser and the full text opens in the reader. */
const TEASER_CHARS = 140;

function firstLine(markdown: string): string {
  const line = markdown.split("\n").find((text) => text.trim()) ?? "";
  // Headings, emphasis and code ticks; underscores stay, they are in stage keys.
  return line.replace(/^#+\s*/, "").replace(/\*\*|`/g, "").trim();
}

/** "stage thrash · review" — enough to recognise a finding; the sentence is one click away. */
function findingLabel(finding: MonitorFinding): string {
  const condition = finding.condition.replace(/_/g, " ");
  return finding.stage_key ? `${condition} · ${finding.stage_key}` : condition;
}

/**
 * A done ticket whose work landed but is not on the base: `done` alone read as
 * shipped, and 679's tests sat three weeks on an unpublished branch that way.
 * The next step is the root's publish card when one failed, else the PR tab,
 * where the tree's PR (or the lack of one) is.
 */
function NotOnBase({ ticketId, delivery }: { ticketId: string; delivery: Delivery }) {
  const base = delivery.base || "the base";
  const failed = Boolean(delivery.publish_card_id);
  const text = failed
    ? `${delivery.root_ref}'s publish failed: ${firstLine(delivery.publish_failure)}`
    : `Landed on ${delivery.branch}; on ${base} once ${delivery.root_ref}'s publish goes out and its PR merges.`;
  return (
    <div className={`attn-row ${failed ? "attn-row--bad" : "attn-row--warn"}`}>
      <span className="attn-label">Not on {base}</span>
      <span className="attn-text" title={failed ? delivery.publish_failure : text}>
        {text}
      </span>
      {failed ? (
        <Button
          variant="plain"
          className="attn-action"
          onClick={() => navigateToTicketTab(delivery.root_id, "approvals")}
        >
          Publish card
        </Button>
      ) : (
        <Button variant="plain" className="attn-action" onClick={() => navigateToTicketTab(ticketId, "pr")}>
          Pull request
        </Button>
      )}
    </div>
  );
}

/**
 * What on this ticket needs a person, in as few lines as there are things.
 *
 * It replaced three stacked cards — the monitor's findings as a bulleted list
 * of full sentences (up to seven on one live ticket), the whole blocking text
 * under a "Rework required" heading even when the ticket was blocked for some
 * other reason, and a red "Run or workflow issue recorded" card that said only
 * that there was something to read elsewhere. Together they pushed the
 * workflow itself below the fold. Now each signal is one line with its next
 * step; the detail is in the Timeline, the reader, or one disclosure away.
 *
 * `failureShown`: the page already leads with the block or the failed run in
 * full (`TicketFailurePanel`), so the one-line teaser for it would repeat it.
 *
 * Renders nothing when nothing needs attention. The monitor's findings stay
 * quiet while loading and on failure: they are advisory, the block and the
 * run errors above them come from the ticket itself, and an always-present
 * row would be noise on the many tickets that are fine.
 */
export function TicketAttention({
  ticket,
  hasRunErrors,
  failureShown = false,
}: {
  ticket: TicketDetail;
  hasRunErrors: boolean;
  failureShown?: boolean;
}) {
  const findingsId = useId();
  const [showFindings, setShowFindings] = useState(false);
  const { data: findings = [] } = useQuery({
    queryKey: ["monitor-findings", ticket.id],
    queryFn: () => api.monitorFindings(ticket.id),
    enabled: Boolean(ticket.id),
  });

  const block = failureShown ? "" : (ticket.blocking_issues?.trim() ?? "");
  const runFailed = hasRunErrors && !failureShown;
  const waiting = (ticket.waiting_on?.length ?? 0) > 0 || (ticket.dependencies ?? []).some(
    (dep) => dep.unmet_reason === "not_landed" && !dep.overridden,
  );
  const notOnBase = ticket.state === "done" && ticket.delivery?.state === "landed_not_on_base";
  if (!block && !runFailed && findings.length === 0 && !waiting && !notOnBase) return null;

  const teaser = firstLine(block);
  const truncated = block.length > TEASER_CHARS || teaser !== block;
  const blockLabel =
    ticket.state === "blocked"
      ? blockHeadline(ticket).label
      : "Rework";
  const openTimeline = () => navigateToTicketTab(ticket.id, "timeline");

  return (
    <section className="attn" aria-label="Needs attention">
      {block ? (
        // Waiting on other tickets is not this ticket's failure: the quiet tone.
        <div className={`attn-row ${ticket.blocked_on === "tickets" ? "attn-row--wait" : "attn-row--bad"}`}>
          <span className="attn-label">{blockLabel}</span>
          <span className="attn-text" title={block}>
            {teaser}
          </span>
          {truncated ? (
            <Button
              variant="plain"
              className="attn-action"
              onClick={() => openReader({ title: blockLabel, content: block })}
            >
              Read
            </Button>
          ) : null}
          <Button variant="plain" className="attn-action" onClick={openTimeline}>
            Timeline
          </Button>
        </div>
      ) : runFailed ? (
        <div className="attn-row attn-row--bad">
          <span className="attn-label">Run failed</span>
          <span className="attn-text">A run on this ticket failed; its output is on the timeline.</span>
          <Button variant="plain" className="attn-action" onClick={openTimeline}>
            Timeline
          </Button>
        </div>
      ) : null}

      {notOnBase && ticket.delivery ? <NotOnBase ticketId={ticket.id} delivery={ticket.delivery} /> : null}

      {/* The tickets to open instead of this one: a rolled-up parent has nothing
          of its own to fix, and a held one starts when these land. */}
      <TicketWaitingOn ticket={ticket} />

      {findings.length > 0 ? (
        <>
          <Button
            variant="plain"
            className="attn-row attn-row--warn attn-toggle"
            aria-expanded={showFindings}
            aria-controls={findingsId}
            onClick={() => setShowFindings((open) => !open)}
          >
            <span className="attn-label">
              Monitor · {findings.length}
            </span>
            <span className="attn-text">{findings.map(findingLabel).join(", ")}</span>
            <span className="attn-chevron" aria-hidden>
              {showFindings ? "▾" : "▸"}
            </span>
          </Button>
          {showFindings ? (
            <ul id={findingsId} className="attn-findings">
              {findings.map((finding) => (
                <li key={`${finding.condition}:${finding.stage_key}`}>
                  {finding.summary}
                  {/* The age, never `occurrences`: that counts reconcile sweeps, and a
                      five-figure number there reads as thrash that never happened. */}
                  {finding.first_seen ? (
                    <span className="attn-age"> first seen {formatRelativeAge(finding.first_seen)}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
