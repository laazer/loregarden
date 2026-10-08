import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../api/client";
import type { TicketDetail } from "../api/client";
import type { MonitorFinding } from "../api/types";
import { formatRelativeAge } from "../lib/timestamps";
import { navigateToTicketTab } from "../lib/useAppNavigation";
import { openReader } from "../state/readerStore";
import { blockKindLabel } from "../utils/blockKinds";
import { Button } from "./ui/Button";
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
 * Renders nothing when nothing needs attention. The monitor's findings stay
 * quiet while loading and on failure: they are advisory, the block and the
 * run errors above them come from the ticket itself, and an always-present
 * row would be noise on the many tickets that are fine.
 */
export function TicketAttention({ ticket, hasRunErrors }: { ticket: TicketDetail; hasRunErrors: boolean }) {
  const findingsId = useId();
  const [showFindings, setShowFindings] = useState(false);
  const { data: findings = [] } = useQuery({
    queryKey: ["monitor-findings", ticket.id],
    queryFn: () => api.monitorFindings(ticket.id),
    enabled: Boolean(ticket.id),
  });

  const block = ticket.blocking_issues?.trim() ?? "";
  if (!block && !hasRunErrors && findings.length === 0) return null;

  const teaser = firstLine(block);
  const truncated = block.length > TEASER_CHARS || teaser !== block;
  const blockLabel =
    ticket.state === "blocked"
      ? `Blocked${ticket.block_kind ? ` · ${blockKindLabel(ticket.block_kind)}` : ""}`
      : "Rework";
  const openTimeline = () => navigateToTicketTab(ticket.id, "timeline");

  return (
    <section className="attn" aria-label="Needs attention">
      {block ? (
        <div className="attn-row attn-row--bad">
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
      ) : hasRunErrors ? (
        <div className="attn-row attn-row--bad">
          <span className="attn-label">Run failed</span>
          <span className="attn-text">A run on this ticket failed; its output is on the timeline.</span>
          <Button variant="plain" className="attn-action" onClick={openTimeline}>
            Timeline
          </Button>
        </div>
      ) : null}

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
