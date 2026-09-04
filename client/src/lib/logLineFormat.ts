import type { LogLine } from "../api/types";

export type LogBodyFormat = "json" | "markdown" | "plain";

export interface LogLineBody {
  /** The mid-dot headline a TOOL line leads with, or "" when the line is all body. */
  headline: string;
  body: string;
  format: LogBodyFormat;
}

const MARKDOWN_MARK = /^(#{1,6} |[-*+] |\d+\. |> |\|.*\|\s*$|```)/;

/**
 * Two marks, not one. A single leading dash is how a diff, a shell flag and
 * half the lines in a stack trace begin, and treating one of those as a list
 * would render a wall of plain output as bullets.
 */
function looksLikeMarkdown(body: string): boolean {
  const lines = body.split("\n");
  if (lines.length < 2) return false;
  let marks = 0;
  for (const line of lines) {
    if (MARKDOWN_MARK.test(line.trimStart())) {
      marks += 1;
      if (marks >= 2) return true;
    }
  }
  return false;
}

/** Re-indented JSON, or null when the body is not JSON after all. */
export function prettyJson(body: string): string | null {
  const trimmed = body.trim();
  const first = trimmed[0];
  const last = trimmed[trimmed.length - 1];
  const bracketed =
    (first === "{" && last === "}") || (first === "[" && last === "]");
  if (!bracketed) return null;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    // Bracketed but unparseable — a truncated payload or a Python repr. It is
    // still plain text, and guessing at a repair would misrepresent it.
    return null;
  }
}

export function splitLogLine(line: LogLine): LogLineBody {
  const text = line.text ?? "";
  const newline = text.indexOf("\n");
  // Only a TOOL line leads with a `$ cmd · status` headline. Splitting any
  // other tag on its first newline would promote a line of output into a
  // header it never was.
  const hasHeadline = line.tag === "TOOL" && newline > -1;
  const headline = hasHeadline ? text.slice(0, newline) : "";
  const body = hasHeadline ? text.slice(newline + 1) : text;

  const json = prettyJson(body);
  if (json !== null) return { headline, body: json, format: "json" };
  if (looksLikeMarkdown(body)) return { headline, body, format: "markdown" };
  return { headline, body, format: "plain" };
}
