/**
 * The reference library's views, labels, filtering and sorting, kept out of
 * the components so the rules are testable on their own.
 *
 * The question the library answers: what is waiting to be surveyed, what have
 * we already read, and what did each source change in our backlog and memory?
 * "Queue" is that first clause, so it is the default view whenever it holds
 * anything.
 */
import type {
  LibraryDetail,
  LibraryList,
  LibraryRow,
  LibraryState,
  LibraryTicket,
  LinkTargetsResult,
  QueueSchedule,
} from "../api/referenceLibraryApi";

export type LibraryView = "queue" | "surveyed" | "refused" | "all";

export const LIBRARY_VIEWS: { key: LibraryView; label: string }[] = [
  { key: "queue", label: "Queue" },
  { key: "surveyed", label: "Surveyed" },
  { key: "refused", label: "Refused" },
  { key: "all", label: "All" },
];

const VIEW_STATES: Record<Exclude<LibraryView, "all">, LibraryState[]> = {
  queue: ["awaiting_approval", "reading", "queued", "candidate"],
  surveyed: ["surveyed"],
  refused: ["refused"],
};

export const STATE_LABELS: Record<LibraryState, string> = {
  awaiting_approval: "Awaiting approval",
  reading: "Being read",
  queued: "Queued",
  candidate: "Not queued",
  surveyed: "Surveyed",
  refused: "Refused",
};

/** What a source in motion is waiting on, shown under its name. */
const IN_MOTION: Record<"awaiting_approval" | "reading" | "queued", string> = {
  awaiting_approval: "Proposed by an agent; nothing is read until it is queued.",
  reading: "A pass is reading it now.",
  queued: "Approved; its next pass waits in the review queue.",
};

/** Queue order: what needs a person first, then what is moving, then the backlog. */
const STATE_ORDER: Record<LibraryState, number> = {
  awaiting_approval: 0,
  reading: 1,
  queued: 2,
  candidate: 3,
  surveyed: 4,
  refused: 5,
};

export function viewCounts(list: LibraryList): Record<LibraryView, number> {
  const sum = (states: LibraryState[]) => states.reduce((n, state) => n + (list.counts[state] ?? 0), 0);
  return {
    queue: sum(VIEW_STATES.queue),
    surveyed: sum(VIEW_STATES.surveyed),
    refused: sum(VIEW_STATES.refused),
    all: list.sources.length,
  };
}

/** The view to open on: the queue when anything is in it. */
export function defaultView(list: LibraryList): LibraryView {
  return viewCounts(list).queue > 0 ? "queue" : "surveyed";
}

export function parseView(raw: string | null): LibraryView | null {
  return LIBRARY_VIEWS.some((view) => view.key === raw) ? (raw as LibraryView) : null;
}

export type LibrarySortKey = "name" | "state" | "passes" | "tickets" | "last_read" | "upstream" | "since_review";
export interface LibrarySort {
  key: LibrarySortKey;
  dir: "ascending" | "descending";
}

export const DEFAULT_SORT: LibrarySort = { key: "state", dir: "ascending" };

export function filterRows(rows: LibraryRow[], view: LibraryView, search: string): LibraryRow[] {
  const needle = search.trim().toLowerCase();
  return rows.filter((row) => {
    if (view !== "all" && !VIEW_STATES[view].includes(row.state)) return false;
    if (!needle) return true;
    return (
      row.name.toLowerCase().includes(needle) ||
      row.url.toLowerCase().includes(needle) ||
      row.licence_name.toLowerCase().includes(needle)
    );
  });
}

function compare(a: LibraryRow, b: LibraryRow, key: LibrarySortKey): number {
  switch (key) {
    case "name":
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    case "state":
      return STATE_ORDER[a.state] - STATE_ORDER[b.state] || a.priority - b.priority;
    case "passes":
      return a.passes - b.passes;
    case "tickets":
      return a.tickets - b.tickets;
    case "last_read":
      return (a.last_read_at ?? "").localeCompare(b.last_read_at ?? "");
    case "upstream":
      return (a.upstream_committed_at ?? "").localeCompare(b.upstream_committed_at ?? "");
    case "since_review":
      // Unknown sorts below any count, so "most changed" leads descending.
      return (a.commits_since_review ?? -1) - (b.commits_since_review ?? -1);
  }
}

export function sortRows(rows: LibraryRow[], sort: LibrarySort): LibraryRow[] {
  const sign = sort.dir === "ascending" ? 1 : -1;
  return [...rows].sort((a, b) => sign * compare(a, b, sort.key) || a.name.localeCompare(b.name));
}

/** Numeric columns read biggest-first on the first click; names A to Z. */
export function nextSort(current: LibrarySort, key: LibrarySortKey): LibrarySort {
  if (current.key === key) return { key, dir: current.dir === "ascending" ? "descending" : "ascending" };
  const numeric = ["passes", "tickets", "last_read", "upstream", "since_review"].includes(key);
  return { key, dir: numeric ? "descending" : "ascending" };
}

const LICENCE_USE_LABELS: Record<string, string> = {
  full: "Full use",
  patterns_only: "Patterns only",
  excluded: "Excluded",
};

/** What the licence lets a survey do — the comparable part, short enough to scan down a column. */
export function licenceUse(row: Pick<LibraryRow, "licence_use">): string {
  return row.licence_use ? LICENCE_USE_LABELS[row.licence_use] ?? row.licence_use : "Not classified";
}

/** The licence in full, for one source's detail. */
export function licenceLabel(row: Pick<LibraryRow, "licence_name" | "licence_use">): string {
  return row.licence_name ? `${row.licence_name} · ${licenceUse(row)}` : licenceUse(row);
}

const OPEN_TICKET_STATES = new Set(["backlog", "in_progress", "blocked", "parked"]);

/** Tickets a source shaped, split into the ones still open and the ones settled. */
export function groupTickets(tickets: LibraryTicket[]): { open: LibraryTicket[]; settled: LibraryTicket[] } {
  const open = tickets.filter((ticket) => OPEN_TICKET_STATES.has(ticket.state));
  const settled = tickets.filter((ticket) => !OPEN_TICKET_STATES.has(ticket.state));
  return { open, settled };
}

export function filterTickets(tickets: LibraryTicket[], search: string): LibraryTicket[] {
  const needle = search.trim().toLowerCase();
  if (!needle) return tickets;
  return tickets.filter(
    (ticket) => ticket.external_id.toLowerCase().includes(needle) || ticket.title.toLowerCase().includes(needle),
  );
}

/** "Oct 13, 2026" — the day is what a person compares; the time is noise here. */
/** The server writes naive UTC; read it as UTC whichever way it arrives. */
function parseUtc(iso: string): Date {
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
}

export function shortDate(iso: string | null): string {
  if (!iso) return "—";
  const date = parseUtc(iso);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export type UpstreamTone = "moved" | "same" | "unknown";

/**
 * How far a repository has moved since we reviewed it. `short` fits a table
 * cell; `long` is the sentence for one source. "Not checked", "could not
 * check" and "no change" are three answers and never read alike.
 */
export function upstreamChange(
  row: Pick<LibraryRow, "upstream_checked_at" | "upstream_committed_at" | "commits_since_review">,
): { short: string; long: string; tone: UpstreamTone } {
  if (!row.upstream_checked_at) return { short: "not checked", long: "not checked yet", tone: "unknown" };
  if (!row.upstream_committed_at) return { short: "couldn’t check", long: "couldn’t check", tone: "unknown" };
  if (row.commits_since_review === null) {
    return { short: "unknown", long: "commits since review unknown", tone: "unknown" };
  }
  if (row.commits_since_review === 0) return { short: "no change", long: "no change since review", tone: "same" };
  const commits = `+${row.commits_since_review} commit${row.commits_since_review === 1 ? "" : "s"}`;
  return { short: commits, long: `${commits} since review`, tone: "moved" };
}

/** "PASS W" → "pass W"; the label-less record pass has no name worth showing. */
function passName(label: string, ordinal: number): string {
  if (!label || label === "RECORD") return `pass ${ordinal}`;
  return label.replace(/^PASS\s+/i, "pass ");
}

/**
 * One factual sentence under a source's name: what happened to it and what it
 * left behind. Not a description of the state; the badge already names that.
 */
export function sourceSummary(detail: LibraryDetail): string {
  const { source } = detail;
  if (source.state === "surveyed") {
    const finished = detail.passes.filter((p) => p.finished_at);
    const last = finished.length ? finished[finished.length - 1].finished_at : source.last_read_at;
    const names = detail.passes.map((p) => passName(p.label, p.ordinal));
    const passes = names.length === 0 ? "" : names.length <= 3 ? ` (${names.join(", ")})` : ` (${names.length} passes)`;
    const when = last ? `Surveyed ${shortDate(last)}${passes}.` : `Surveyed${passes}.`;
    const n = detail.tickets.length;
    const tickets = n ? `${n} ticket${n === 1 ? " carries" : "s carry"} its findings` : "No ticket is linked to it";
    const unmatched = detail.unresolved_refs.length
      ? `, plus ${detail.unresolved_refs.length} old references that match no ticket`
      : "";
    return `${when} ${tickets}${unmatched}.`;
  }
  if (source.state === "refused") {
    return detail.refused_reason ? "Not surveyed; the reason is below." : "Not surveyed, and the record gives no reason.";
  }
  if (source.state === "candidate") {
    return "Listed in the old record but never queued.";
  }
  return IN_MOTION[source.state];
}

/** "next", "2nd in line", "3rd in line"… */
export function placeInLine(position: number): string {
  if (position <= 1) return "next in line";
  const rem100 = position % 100;
  const suffix =
    rem100 >= 11 && rem100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[position % 10] ?? "th";
  return `${position}${suffix} in line`;
}

/** When the feeder next checks the queue, in words, from `now`. */
export function nextCheckText(schedule: QueueSchedule, now: Date = new Date()): string {
  const every = Math.max(1, Math.round(schedule.interval_seconds / 60));
  if (!schedule.next_tick_at) return `within ${every} minute${every === 1 ? "" : "s"}`;
  const ms = parseUtc(schedule.next_tick_at).getTime() - now.getTime();
  if (ms <= 60_000) return "within a minute";
  const minutes = Math.round(ms / 60_000);
  return `in about ${minutes} minute${minutes === 1 ? "" : "s"}`;
}

/** The URLs in pasted text: split on commas, tabs and new lines, blanks and repeats dropped. */
export function splitUrls(text: string): string[] {
  return [...new Set(text.split(/[,\t\r\n]+/).map((part) => part.trim()).filter(Boolean))];
}

function pluralOf(n: number, one: string): string {
  return `${n} ${n === 1 ? one : `${one}s`}`;
}

/** What one link call did, in a sentence a person can check against the source. */
export function linkTargetsSummary(name: string, result: LinkTargetsResult): string {
  const parts = [];
  if (result.attached.length) {
    parts.push(
      `${name} now reads for ${result.attached.join(", ")}` +
        (result.scoped_pass ? ` (pass ${result.scoped_pass} reads it again for them)` : ""),
    );
  }
  for (const link of result.initiatives) {
    const extra = [
      link.settled_left_out ? `${pluralOf(link.settled_left_out, "settled ticket")} stayed out` : "",
      link.skipped.length ? `${link.skipped.length} it already covers` : "",
    ].filter(Boolean);
    parts.push(
      `${pluralOf(link.added.length, "open ticket")} joined ${link.initiative}` +
        (extra.length ? ` (${extra.join("; ")})` : ""),
    );
  }
  return `${parts.join("; ")}.`;
}


/** "Rust · ★ 2.3k · agents, verification": what GitHub says about it, at a glance. */
export function sourceFacts(row: Pick<LibraryRow, "language" | "stars" | "topics">): string {
  const stars =
    row.stars === null ? "" : `★ ${row.stars >= 1000 ? `${(row.stars / 1000).toFixed(1).replace(/\.0$/, "")}k` : row.stars}`;
  return [row.language, stars, row.topics].filter(Boolean).join(" · ");
}
