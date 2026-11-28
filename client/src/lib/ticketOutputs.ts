import type { TicketArtifactItem } from "../api/types";
import { openReader, type ReaderBadge, type ReaderPartial } from "../state/readerStore";
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
 * Where the rest of a shortened row lives, so the reader can fill itself in.
 *
 * `undefined` for a row the feed returned whole, which is every row under the
 * feed's per-row bound — `openReader` then has nothing to fetch.
 */
function partialOf(item: TicketArtifactItem, ticketId: string): ReaderPartial | undefined {
  if (!item.content_truncated) return undefined;
  return {
    ticketId,
    artifactId: item.id,
    shownBytes: item.content_shown_bytes,
    totalBytes: item.content_bytes,
  };
}

/**
 * The `partial` tag's accessible name, in the operator's own question: am I
 * looking at the whole of this output, or only its beginning?
 *
 * The per-row size belongs here and not in a toolbar note, because two
 * shortened rows show different amounts of themselves.
 */
export function partialTagLabel(item: TicketArtifactItem): string {
  const whole = `open the row to read all ${formatBytes(item.content_bytes)}`;
  const shown =
    typeof item.content_shown_bytes === "number"
      ? `only the first ${formatBytes(item.content_shown_bytes)} is in this table`
      : "only the beginning of it is in this table";
  return `partial — ${shown}; ${whole}`;
}

/**
 * Open an output in the reader, placed: what kind it is, which stage it came
 * from, when, against which commit. `stage` is where the caller placed it on
 * the timeline, when that is more than the row itself records.
 *
 * Synchronous, deliberately: the row already carries the head of its content,
 * which is where a reader starts, so the pane opens on this call and the rest
 * (when the feed shortened the row) arrives behind it. `ticketId` is the
 * address the reader completes the document from.
 */
export function openOutput(
  item: TicketArtifactItem,
  options: { ticketId: string; stage?: string },
): void {
  const place = [
    item.kind,
    options.stage || item.stage_key,
    formatLocalTimestamp(item.created_at),
    item.commit_sha ? `commit ${item.commit_sha.slice(0, 8)}` : "",
    formatBytes(item.content_bytes),
  ].filter(Boolean);
  openReader({
    id: item.id,
    title: outputTitle(item),
    subtitle: place.join(" · "),
    badge: outputBadge(item) ?? undefined,
    content: item.content,
    partial: partialOf(item, options.ticketId),
  });
}
