/**
 * Render a server timestamp in the viewer's own timezone.
 *
 * Two hazards this exists to absorb:
 *
 * 1. **Offset-less strings.** Timestamps are stored UTC-aware but land in
 *    SQLite's zone-less DATETIME, so some endpoints still emit
 *    `2026-08-08T14:19:57.465660` with no `Z`. ECMAScript parses that as *local*
 *    time, silently shifting a UTC instant by the viewer's whole offset. Anything
 *    offset-less is therefore read as the UTC it actually is.
 * 2. **Mixed formats.** Values computed in-request (rather than loaded from a
 *    row) already carry `+00:00`, so both shapes reach the same parser.
 *
 * The zone abbreviation is part of the output on purpose: a bare "2:19 PM" is
 * the thing that made run times ambiguous in the first place.
 */

const OFFSET_SUFFIX = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/** Parse a server timestamp, reading an offset-less value as UTC. Null if unusable. */
export function parseTimestamp(value: string | null | undefined): Date | null {
  if (!value) return null;
  const normalized = OFFSET_SUFFIX.test(value) ? value : `${value}Z`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** "Aug 8, 2026, 2:19:57 PM PDT" — full local date and time with the zone named. */
export function formatLocalTimestamp(value: string | null | undefined, fallback = "—"): string {
  const parsed = parseTimestamp(value);
  if (!parsed) return fallback;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
}

/** "3d ago" / "4h ago" / "12m ago" / "just now" — coarse age, empty if unparseable. */
export function formatRelativeAge(value: string | null | undefined, now: Date = new Date()): string {
  const parsed = parseTimestamp(value);
  if (!parsed) return "";
  const seconds = Math.round((now.getTime() - parsed.getTime()) / 1000);
  if (seconds < 0) return "";
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/**
 * When a run actually happened, preferring the stamp that answers it best:
 * a finished run is dated by its end, a started one by its start, and a run
 * that never started at all by its creation.
 */
export function runTimestamp(run: {
  finished_at?: string | null;
  started_at?: string | null;
  created_at?: string | null;
}): string | null {
  return run.finished_at ?? run.started_at ?? run.created_at ?? null;
}
