import type { LogLine, TicketDetail } from "../api/client";

export function formatLogExcerpt(
  lines: LogLine[],
  live?: string | null,
  maxLines = 24,
): string {
  const excerpt = lines
    .slice(-maxLines)
    .map((line) => `${line.time} ${line.tag} ${line.text}`)
    .join("\n");
  if (live?.trim()) {
    return excerpt ? `${excerpt}\n\n[live] ${live.trim()}` : `[live] ${live.trim()}`;
  }
  return excerpt;
}

export function artifactLogLines(artifacts: TicketDetail["artifacts"] | undefined): LogLine[] {
  return artifacts?.logs ?? [];
}
