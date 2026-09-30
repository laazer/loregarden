/**
 * Turning an agent-written JSON body into something a person reads.
 *
 * Artifact bodies are free-form: 3,881 `context` rows, 259 `review` rows and
 * the rest share no schema, only a habit — prose lives in string fields
 * (`markdown`, `document`, `summary`, `detail`), lists in arrays, and short
 * facts (`verdict`, `status`, `task_count`) in scalars. These helpers sort a
 * value along those lines so the renderer can put facts in a compact grid and
 * prose under headings, instead of `JSON.stringify` escaping every newline.
 */

export type JsonScalar = string | number | boolean | null;

/** Fields that carry the document itself, shown first and without a heading when alone. */
const BODY_KEYS = [
  "markdown",
  "document",
  "spec_document",
  "body",
  "content",
  "message",
  "summary",
  "note",
  "detail",
] as const;

/** Keys that name an item in a list of objects, in order of preference. */
const LABEL_KEYS = ["title", "name", "finding", "claim", "summary", "command", "id"] as const;

/** A string this short, on one line, reads as a fact rather than prose. */
const FACT_MAX_CHARS = 80;

export function isScalar(value: unknown): value is JsonScalar {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A scalar short enough to sit in a key/value grid. */
export function isFact(value: unknown): boolean {
  if (value === null || typeof value === "number" || typeof value === "boolean") return true;
  return typeof value === "string" && value.length <= FACT_MAX_CHARS && !value.includes("\n");
}

/** `acceptance_criteria_status` → `Acceptance criteria status`. */
export function humanizeKey(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").trim();
  if (!spaced) return key;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

export function formatScalar(value: JsonScalar): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

/** Whether a value has anything in it worth a heading. */
export function isEmptyValue(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  if (isRecord(value)) return Object.keys(value).length === 0;
  return false;
}

export interface SplitRecord {
  facts: [string, JsonScalar][];
  sections: [string, unknown][];
}

/**
 * Split an object into short facts and headed sections, body fields first.
 * Empty fields are dropped: an empty `open_questions: []` is not a section.
 */
export function splitRecord(record: Record<string, unknown>): SplitRecord {
  const facts: [string, JsonScalar][] = [];
  const body: [string, unknown][] = [];
  const rest: [string, unknown][] = [];
  for (const [key, value] of Object.entries(record)) {
    if (isEmptyValue(value)) continue;
    if (isFact(value)) {
      facts.push([key, value as JsonScalar]);
    } else if ((BODY_KEYS as readonly string[]).includes(key)) {
      body.push([key, value]);
    } else {
      rest.push([key, value]);
    }
  }
  body.sort(
    ([a], [b]) =>
      (BODY_KEYS as readonly string[]).indexOf(a) - (BODY_KEYS as readonly string[]).indexOf(b),
  );
  return { facts, sections: [...body, ...rest] };
}

/** The field that names one object in a list — `title`, `name`, `finding`… */
export function recordLabel(record: Record<string, unknown>): string | null {
  for (const key of LABEL_KEYS) {
    const value = record[key];
    if (typeof value === "string" && value.trim() && value.length <= 160 && !value.includes("\n")) {
      return value.trim();
    }
  }
  return null;
}

/**
 * The first readable prose in a value — for a one-line preview under a
 * collapsed row. Walks body fields first, then anything else, depth-first.
 */
export function firstProse(value: unknown, depth = 0): string | null {
  if (depth > 4) return null;
  if (typeof value === "string") return value.trim() || null;
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = firstProse(item, depth + 1);
      if (found) return found;
    }
    return null;
  }
  if (!isRecord(value)) return null;
  const { sections, facts } = splitRecord(value);
  for (const [, section] of sections) {
    const found = firstProse(section, depth + 1);
    if (found) return found;
  }
  const textFact = facts.find(([, fact]) => typeof fact === "string");
  return textFact ? String(textFact[1]) : null;
}

/** Collapse markdown to a single plain line for a preview. */
export function plainPreview(text: string, maxChars = 180): string {
  const flat = text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#>*_`|[\]]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return flat.length > maxChars ? `${flat.slice(0, maxChars - 1)}…` : flat;
}

/** Whether markdown is long enough that a dedicated reader earns its button. */
export function isLongProse(text: string): boolean {
  return text.length > 600 || text.split("\n").length > 12;
}
