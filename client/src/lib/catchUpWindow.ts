/**
 * The "since you were away" window: the last visit by default, or a fixed span.
 *
 * The last visit is a per-viewer convenience, so it lives in this browser's
 * storage. When storage is unavailable (a private window, blocked site data)
 * the page falls back to the 7-day span and says so rather than pretending.
 */

export const CATCH_UP_SPANS = ["last_visit", "1d", "7d", "14d", "30d"] as const;
export type CatchUpSpan = (typeof CATCH_UP_SPANS)[number];

export const CATCH_UP_LABELS: Record<CatchUpSpan, string> = {
  last_visit: "Since my last visit",
  "1d": "Last 24 hours",
  "7d": "Last 7 days",
  "14d": "Last 14 days",
  "30d": "Last 30 days",
};

const DAY_MS = 24 * 60 * 60 * 1000;
const SPAN_DAYS: Record<Exclude<CatchUpSpan, "last_visit">, number> = { "1d": 1, "7d": 7, "14d": 14, "30d": 30 };
/** The server answers at most 180 days; an older visit falls back to `FALLBACK_SPAN`. */
const MAX_LAST_VISIT_DAYS = 180;
export const FALLBACK_SPAN: Exclude<CatchUpSpan, "last_visit"> = "7d";

const visitKey = (slug: string) => `loregarden.workspace-dashboard.visit.${slug}`;
/** Reopening within this long is the same visit: a reload must not empty the digest. */
export const SAME_VISIT_MS = 60 * 60 * 1000;

interface StoredVisit {
  /** When the visit before this one began; the digest's anchor. */
  previous: string | null;
  /** When this visit began. */
  current: string;
}

export interface Visit {
  /** When the previous visit began, or null on a first visit or unreadable storage. */
  lastVisit: Date | null;
  /** False when storage refused; the page says the visit will not be remembered. */
  remembered: boolean;
}

function parse(raw: string | null): StoredVisit | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<StoredVisit>;
    return typeof value.current === "string" ? { previous: value.previous ?? null, current: value.current } : null;
  } catch {
    /* silent-ok: a corrupt entry is treated as a first visit and overwritten below */
    return null;
  }
}

const asDate = (value: string | null): Date | null => {
  if (!value) return null;
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? null : when;
};

/**
 * Start (or continue) a visit to ``slug``'s dashboard and return its anchor.
 *
 * Opening the page again within `SAME_VISIT_MS` continues the visit, keeping
 * the same anchor, so a reload does not reset "since you were away" to now.
 */
export function beginVisit(slug: string, now: Date): Visit {
  let stored: StoredVisit | null;
  try {
    stored = parse(window.localStorage.getItem(visitKey(slug)));
  } catch {
    /* silent-ok: storage blocked; remembered=false is rendered and the span falls back */
    return { lastVisit: null, remembered: false };
  }
  const began = asDate(stored?.current ?? null);
  const next: StoredVisit =
    stored !== null && began !== null && now.getTime() - began.getTime() < SAME_VISIT_MS
      ? stored
      : { previous: stored?.current ?? null, current: now.toISOString() };
  try {
    window.localStorage.setItem(visitKey(slug), JSON.stringify(next));
  } catch {
    /* silent-ok: storage refused the write; remembered=false is rendered */
    return { lastVisit: asDate(next.previous), remembered: false };
  }
  return { lastVisit: asDate(next.previous), remembered: true };
}

export interface CatchUpWindow {
  since: Date;
  /** The span actually used: `last_visit` falls back when there is no usable visit. */
  span: CatchUpSpan;
}

export function resolveCatchUpWindow(span: CatchUpSpan, lastVisit: Date | null, now: Date): CatchUpWindow {
  if (span === "last_visit") {
    if (lastVisit && now.getTime() - lastVisit.getTime() <= MAX_LAST_VISIT_DAYS * DAY_MS && lastVisit < now) {
      return { since: lastVisit, span };
    }
    span = FALLBACK_SPAN;
  }
  return { since: new Date(now.getTime() - SPAN_DAYS[span] * DAY_MS), span };
}
