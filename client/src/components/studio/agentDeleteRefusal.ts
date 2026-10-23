/**
 * The shape a refused agent delete arrives in, and how to read it off the error.
 *
 * `DELETE /api/studio/agents/{slug}` answers 409 with
 * `{"detail": "<sentence>", "references": {"blocking": [...], "historical": [...]}}`.
 * The sentence is what the toast carries; the structured halves are what the
 * panel notice needs to build a link per referencing workflow. It rides on
 * `ApiError.body`, so neither half costs a second request.
 *
 * Every other delete failure — a 500, a dropped connection, the 404 of an agent
 * somebody else removed — has no `references` at all. That is why this returns
 * `null` rather than an empty refusal: a notice listing no references reads as
 * "nothing is in the way" on a delete that did not happen.
 */

/** One reference class: its ids, truncated at ten, beside its true total. */
export interface AgentReferenceClass {
  kind: string;
  ids: string[];
  count: number;
}

export interface AgentDeleteRefusal {
  detail: string;
  references: {
    /** Any non-empty class here is why the delete was refused. */
    blocking: AgentReferenceClass[];
    /** Counted and reported, never blocking: the append-only run ledger. */
    historical: AgentReferenceClass[];
  };
}

/** The reference kinds that name a place in Studio → Workflows.
 *
 * Navigability is derived here rather than carried on the wire: it is a
 * frontend concern, and a ticket id rendered as a link to
 * Studio → Workflows → <ticket id> lands the operator on nothing.
 */
const NAVIGABLE_KINDS = new Set(["workflow_template", "studio_workflow"]);

export function isNavigableKind(kind: string): boolean {
  return NAVIGABLE_KINDS.has(kind);
}

/** The part of a Studio draft that says which template it publishes to. */
export interface WorkflowDraftRef {
  slug: string;
  published_template_slug?: string;
}

/**
 * Where a reference id actually opens in Workflow Studio, or `null`.
 *
 * Studio → Workflows addresses **drafts**, so the id the scan reports is not
 * always the id to navigate to. The scan names `workflow_templates.slug`, and
 * for a studio-generated template that differs from the draft behind it by a
 * `studio-` prefix (`studio-blobert-ddd` → `blobert-ddd`). Handing the template
 * slug to `navigateToStudioWorkflow` lands on a blank Untitled form, because
 * the editor's guard effect redirects an id that is not in the draft list.
 *
 * The mapping is read off `published_template_slug` rather than by stripping
 * the prefix: a draft records the template it published, and a hand-authored
 * template with no draft behind it has no destination at all — which is why
 * this returns `null` instead of a slug nobody can open.
 */
export function resolveWorkflowDestination(
  kind: string,
  id: string,
  drafts: readonly WorkflowDraftRef[],
): string | null {
  if (!isNavigableKind(kind) || !id) return null;
  if (kind === "studio_workflow") {
    return drafts.some((draft) => draft.slug === id) ? id : null;
  }
  const draft = drafts.find((item) => item.published_template_slug === id);
  return draft ? draft.slug : null;
}

/** Human wording for the kinds we know, and the raw kind for one we do not.
 *
 * The blocking set is specified to be demotable in one line, so a class will be
 * added or moved after this ships. Falling back to the server's own kind string
 * reports it rather than dropping it.
 */
const KIND_LABELS: Record<string, string> = {
  workflow_template: "workflow template(s)",
  studio_workflow: "draft workflow(s)",
  ticket_next_agent: "live ticket(s) pinned through next_agent",
  ticket_scope_reroute_agent: "live ticket(s) pinned through scope_reroute_agent",
  agent_run: "past agent run(s)",
  mcp_tool_call: "past MCP tool call(s)",
  stage_fanout_group: "fan-out group snapshot(s)",
};

export function referenceKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
}

function readClasses(value: unknown): AgentReferenceClass[] {
  if (!Array.isArray(value)) return [];
  const classes: AgentReferenceClass[] = [];
  for (const entry of value) {
    const candidate = entry as Partial<AgentReferenceClass> | null;
    if (!candidate || typeof candidate.kind !== "string") continue;
    classes.push({
      kind: candidate.kind,
      ids: Array.isArray(candidate.ids) ? candidate.ids.map(String) : [],
      count: typeof candidate.count === "number" ? candidate.count : 0,
    });
  }
  return classes;
}

/**
 * The refusal carried by a rejected delete, or `null` when it carried none.
 *
 * Keyed on the body having a `references` object rather than on the status: the
 * status that carries a refusal is the server's to choose, and a parse keyed on
 * 409 would go quietly blind the day it changes.
 */
export function readAgentDeleteRefusal(error: unknown): AgentDeleteRefusal | null {
  const body = (error as { body?: unknown } | null)?.body as
    | { detail?: unknown; references?: unknown }
    | null
    | undefined;
  if (!body || typeof body !== "object") return null;
  const references = body.references as
    | { blocking?: unknown; historical?: unknown }
    | null
    | undefined;
  if (!references || typeof references !== "object") return null;
  const detail =
    typeof body.detail === "string" && body.detail.trim()
      ? body.detail
      : "This agent cannot be deleted while something still references it.";
  return {
    detail,
    references: {
      blocking: readClasses(references.blocking),
      historical: readClasses(references.historical),
    },
  };
}
