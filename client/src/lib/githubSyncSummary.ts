import type { GithubLinkSyncResult, GithubSyncField } from "../api/githubIssueApi";

const FIELD_LABEL: Record<GithubSyncField, string> = {
  title: "title",
  body: "description",
  closure: "open/closed state",
};

export function syncFieldList(fields: GithubSyncField[]): string {
  return fields.map((field) => FIELD_LABEL[field]).join(", ");
}

/** One line saying what a link's sync moved, in which direction. */
export function describeLinkSync(result: GithubLinkSyncResult): string {
  const parts: string[] = [];
  if (result.pulled.length) parts.push(`Pulled ${syncFieldList(result.pulled)} from GitHub`);
  if (result.pushed.length) parts.push(`Pushed ${syncFieldList(result.pushed)} to GitHub`);
  if (!parts.length && !result.conflicts.length) return "Already in sync.";
  return parts.join(" · ");
}
