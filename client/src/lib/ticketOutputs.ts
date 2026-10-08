import type { TicketArtifactItem } from "../api/types";
import { openReader, type ReaderBadge } from "../state/readerStore";
import { isRecord } from "./structuredContent";
import { isErrorOutput, reportVerdict } from "./ticketTimeline";
import { formatLocalTimestamp, parseTimestamp } from "./timestamps";

/** How an artifact row is named, dated and opened, wherever it is listed. */

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** "Sep 26, 17:44" — a ticket's history spans days, so the date stays. */
export function formatShortTime(iso: string | null): string {
  const at = parseTimestamp(iso);
  if (!at) return "—";
  return at.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function outputTitle(item: TicketArtifactItem): string {
  return item.title || item.kind;
}

const PASSING = new Set(["pass", "passed", "approve", "approved"]);
const FAILING = new Set(["fail", "failed", "reject", "rejected", "needs_rework"]);

export function verdictTone(status: string): ReaderBadge["tone"] {
  return PASSING.has(status) ? "good" : FAILING.has(status) ? "bad" : "warn";
}

/** A handoff's checklist score: `required_items_met` of `total_required_items`. */
function handoffScore(content: unknown): ReaderBadge | null {
  const handoff = isRecord(content) && isRecord(content.handoff) ? content.handoff : null;
  if (!handoff) return null;
  const met = handoff.required_items_met;
  const total = handoff.total_required_items;
  if (typeof met !== "number" || typeof total !== "number" || total === 0) return null;
  return { text: `${met}/${total} required met`, tone: met === total ? "good" : "warn" };
}

/** What an output concluded, when it says: the reader shows it beside the title. */
export function outputBadge(item: TicketArtifactItem): ReaderBadge | null {
  const verdict = reportVerdict(item.content);
  if (verdict) {
    const confidence = verdict.confidence !== null ? ` · ${verdict.confidence.toFixed(2)}` : "";
    return { text: `${verdict.status}${confidence}`, tone: verdictTone(verdict.status) };
  }
  if (isErrorOutput(item)) return { text: "error", tone: "bad" };
  return handoffScore(item.content);
}

/**
 * Open an output in the reader, placed: what kind it is, which stage it came
 * from, when, against which commit. `stage` is where the caller placed it on
 * the timeline, when that is more than the row itself records.
 */
export function openOutput(item: TicketArtifactItem, stage?: string): void {
  const place = [
    item.kind,
    stage || item.stage_key,
    formatLocalTimestamp(item.created_at),
    item.commit_sha ? `commit ${item.commit_sha.slice(0, 8)}` : "",
    formatBytes(item.content_bytes),
  ].filter(Boolean);
  openReader({
    title: outputTitle(item),
    subtitle: place.join(" · "),
    badge: outputBadge(item) ?? undefined,
    content: item.content,
  });
}
