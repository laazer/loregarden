import type { GraphNode, InferredGroup, InferredGroupKind } from "../api/memoryApi";

/**
 * Reading the knowledge graph's inferred groups: as edges for the map, and as
 * "shares a ticket / milestone / tag with" lists for a record's panel.
 */

export const INFERRED_KIND_LABELS: Record<InferredGroupKind, string> = {
  same_ticket: "Same ticket",
  same_milestone: "Same milestone",
  shared_tag: "Shared tag",
};

/** An edge drawn for an inferred group. Shaped like a relation for the layout. */
export interface InferredEdge {
  id: string;
  source_id: string;
  target_id: string;
  kind: InferredGroupKind;
  label: string;
}

/**
 * One group as a star around its first member — n-1 edges rather than the
 * n(n-1)/2 of a clique, which for a nine-record milestone would be 36 lines
 * saying the same thing. Enough for the layout to pull the group together.
 */
export function inferredEdges(groups: InferredGroup[]): InferredEdge[] {
  const edges: InferredEdge[] = [];
  for (const group of groups) {
    const [hub, ...rest] = group.node_ids;
    for (const member of rest) {
      edges.push({
        id: `${group.kind}:${group.key}:${member}`,
        source_id: hub,
        target_id: member,
        kind: group.kind,
        label: group.label,
      });
    }
  }
  return edges;
}

/** The groups one record belongs to. */
export function groupsFor(nodeId: string, groups: InferredGroup[]): InferredGroup[] {
  return groups.filter((group) => group.node_ids.includes(nodeId));
}

/** A title that is only a type and a ticket id — "Learning — lg-bug-hole-574". */
const GENERIC_TITLE = /^(learning|memory)\s*[—–-]\s*\S+$/i;
const TITLE_CHARS = 90;

/**
 * What to call a record. Most learnings are titled by the ticket they came
 * from, which says nothing about the lesson; when the title is only that, the
 * first line of the body stands in for it — a markdown heading or bold lead
 * when there is one, otherwise the opening sentence.
 */
export function recordTitle(node: Pick<GraphNode, "title" | "excerpt">): string {
  if (!GENERIC_TITLE.test(node.title.trim()) || !node.excerpt.trim()) return node.title;
  const text = node.excerpt.trim();
  const bold = /^\*\*(.+?)\*\*/.exec(text);
  const lead = bold ? bold[1] : text.replace(/^#+\s*/, "").split(/(?<=[.!?])\s|\s#+\s/)[0];
  if (lead.length <= TITLE_CHARS) return lead;
  const cut = lead.slice(0, TITLE_CHARS);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 40 ? cut.lastIndexOf(" ") : TITLE_CHARS)}…`;
}
