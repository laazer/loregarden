/**
 * Surface S2: what is still using this agent, and where to go change it.
 *
 * **Question:** "can I remove this agent, and if not, what is still using it
 * that I must change first?"
 * **Action:** a control per referencing workflow or template, which leaves the
 * agent editor for Studio → Workflows → that slug, where the stage naming the
 * agent can be removed.
 * **Real data:** measured on the live database, the only two agents whose
 * Delete button renders are both named by the `studio-blobert-ddd` template and
 * the `blobert-ddd` draft, so the refusal is 100% of what this install's Delete
 * button produces rather than its rare branch. `planner` would carry 8
 * templates, 3 drafts, 65 live pins, 491 runs and 379 tool calls — which is why
 * the server truncates each class at ten ids and sends the true count beside
 * them, and why this renders the count even for a class whose ids are all gone.
 *
 * The automatic toast still fires. Nine seconds of text holds no link, and the
 * operator's next step is a place to go.
 */

import {
  referenceKindLabel,
  type AgentDeleteRefusal,
  type AgentReferenceClass,
} from "./agentDeleteRefusal";
import { Button } from "../ui/Button";

interface AgentDeleteRefusalNoticeProps {
  /** The last delete's refusal, or `null` when the last delete did not refuse. */
  refusal: AgentDeleteRefusal | null;
  /** Open Studio → Workflows → `slug`. */
  onOpenWorkflow: (slug: string) => void;
  /**
   * The draft slug a reference id opens, or `null` when it opens nothing.
   *
   * A reference class being navigable is not enough: Studio → Workflows
   * addresses drafts, and the scan reports a template's own slug, which for a
   * studio-generated template differs from the draft behind it. An id with no
   * draft behind it renders as text rather than as a control that lands on a
   * blank form.
   */
  resolveWorkflowSlug: (kind: string, id: string) => string | null;
}

export function AgentDeleteRefusalNotice({
  refusal,
  onOpenWorkflow,
  resolveWorkflowSlug,
}: AgentDeleteRefusalNoticeProps) {
  if (!refusal) return null;

  const { blocking, historical } = refusal.references;
  return (
    <div className="studio-drift-notice" role="status">
      <strong>{refusal.detail}</strong>
      {blocking.length > 0 && (
        <ul>
          {blocking.map((entry) => (
            <ReferenceRow
              key={`blocking-${entry.kind}`}
              entry={entry}
              onOpenWorkflow={onOpenWorkflow}
              resolveWorkflowSlug={resolveWorkflowSlug}
            />
          ))}
        </ul>
      )}
      {historical.length > 0 && (
        <>
          {/* Kept apart from the blocking half deliberately: a ledger row is
              append-only and nothing the operator does clears it, so showing
              one as something to go and fix is a refusal with no exit. */}
          <div>Already recorded, and not in the way:</div>
          <ul>
            {historical.map((entry) => (
              <ReferenceRow key={`historical-${entry.kind}`} entry={entry} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** One reference class: its total, then its ids — as controls where they name a
 *  workflow the operator can go and edit, and as text where they do not. */
function ReferenceRow({
  entry,
  onOpenWorkflow,
  resolveWorkflowSlug,
}: {
  entry: AgentReferenceClass;
  onOpenWorkflow?: (slug: string) => void;
  resolveWorkflowSlug?: (kind: string, id: string) => string | null;
}) {
  return (
    <li>
      {entry.count} {referenceKindLabel(entry.kind)}
      {entry.ids.length > 0 && (
        <>
          {": "}
          {entry.ids.map((id, index) => {
            const destination = onOpenWorkflow
              ? (resolveWorkflowSlug?.(entry.kind, id) ?? null)
              : null;
            return (
            <span key={id}>
              {index > 0 && ", "}
              {destination ? (
                <Button
                  variant="plain"
                  compact
                  className="studio-reference-link"
                  onClick={() => onOpenWorkflow?.(destination)}
                >
                  {id}
                </Button>
              ) : (
                <code className="studio-reference-id">{id}</code>
              )}
            </span>
            );
          })}
          {entry.ids.length < entry.count && <span> …</span>}
        </>
      )}
    </li>
  );
}
