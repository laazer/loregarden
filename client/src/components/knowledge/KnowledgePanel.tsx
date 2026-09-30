/**
 * One record in full, beside the graph — ending, always, in Origin & Evidence.
 *
 * Provenance is a fact of the record rather than metadata behind a tab, so it
 * is the last section and never collapsed. A record written before provenance
 * was recorded says "Origin unknown": NULL is rendered as unknown, never as a
 * guessed default.
 *
 * Lifecycle and revision pills beyond `discredited`, code groundings
 * (lg-code-knowledge-368), drift states (369) and dual timelines (661) will
 * attach here when those records exist; nothing is rendered for them now.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { GraphNode, InferredGroup, MemoryNodeDetail } from "../../api/memoryApi";
import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";
import { NODE_TYPE_LABELS } from "../../lib/knowledgeLayout";
import { memoryPath, ticketPath } from "../../lib/appNavigation";
import { groupsFor, INFERRED_KIND_LABELS, recordTitle } from "../../lib/memoryInferred";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { LearningConfidenceReadout } from "../memory/LearningConfidenceReadout";
import { describeError, errorStatus, toastActionFailed } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { MarkdownContent } from "../chat/MarkdownContent";
import { PaneSkeleton } from "../ui/PaneSkeleton";

const ORIGIN_LABELS = { agent: "Agent", human: "Human", import: "Import" } as const;
/** What an `origin_ref` names, per kind (see the server's `MemoryOriginKind`). */
const REF_PREFIX = { agent: "run ", human: "", import: "" } as const;

function Origin({ record }: { record: MemoryNodeDetail }) {
  const recorded = formatLocalTimestamp(record.created_at);
  const parts = record.origin_kind
    ? [
        ORIGIN_LABELS[record.origin_kind],
        record.origin_ref
          ? `${REF_PREFIX[record.origin_kind]}${record.origin_ref}`
          : "reference not recorded",
        recorded,
      ]
    : ["Origin unknown", `recorded ${recorded}`];
  return (
    <section className="kb-origin" aria-labelledby="kb-origin-title">
      <h3 id="kb-origin-title" className="kb-section-title">
        Origin &amp; Evidence
      </h3>
      <p data-testid="kb-origin">{parts.join(" · ")}</p>
    </section>
  );
}

function Neighbours({
  record,
  onSelect,
}: {
  record: MemoryNodeDetail;
  onSelect: (nodeId: string) => void;
}) {
  if (record.relations.length === 0) {
    return <p className="kb-muted">No recorded relationships.</p>;
  }
  return (
    <ul className="kb-neighbours" aria-label="Related records">
      {record.relations.map((edge) => (
        <li key={edge.id}>
          <span className="kb-muted">
            {edge.direction === "out" ? "→" : "←"} {edge.relation_type.replace("_", " ")}
          </span>{" "}
          <button type="button" className="kb-link-button" onClick={() => onSelect(edge.node_id)}>
            {edge.title}
          </button>
          {edge.discredited && <span className="kb-pill">Discredited</span>}
        </li>
      ))}
    </ul>
  );
}

/**
 * The records this one is grouped with by a shared ticket, milestone or tag —
 * the answer to "what else do we know about this?" when no agent recorded an
 * edge, which on the live graph was every time.
 */
function SharedWith({
  nodeId,
  groups,
  nodes,
  onSelect,
}: {
  nodeId: string;
  groups: InferredGroup[];
  nodes: GraphNode[];
  onSelect: (nodeId: string) => void;
}) {
  const mine = groupsFor(nodeId, groups);
  if (mine.length === 0) {
    return <p className="kb-muted">Shares no ticket, milestone or specific tag with the records on the map.</p>;
  }
  const byId = new Map(nodes.map((node) => [node.id, node]));
  return (
    <>
      {mine.map((group) => (
        <div key={`${group.kind}:${group.key}`} className="kb-shared-group">
          <div className="kb-shared-label">
            {INFERRED_KIND_LABELS[group.kind]}: {group.label}
          </div>
          <ul className="kb-neighbours">
            {group.node_ids
              .filter((id) => id !== nodeId)
              .map((id) => {
                const node = byId.get(id);
                return (
                  <li key={id}>
                    <button type="button" className="kb-link-button" onClick={() => onSelect(id)}>
                      {node ? recordTitle(node) : id}
                    </button>
                  </li>
                );
              })}
          </ul>
        </div>
      ))}
    </>
  );
}

function Record({
  record,
  groups,
  nodes,
  onSelect,
}: {
  record: MemoryNodeDetail;
  groups: InferredGroup[];
  nodes: GraphNode[];
  onSelect: (nodeId: string) => void;
}) {
  const title = recordTitle({ title: record.title, excerpt: record.body.replace(/\s+/g, " ") });
  return (
    <>
      <div className="state-label">{NODE_TYPE_LABELS[record.node_type]}</div>
      <h2 id="kb-panel-title" className="kb-panel-title">
        {title}
      </h2>
      {title !== record.title && <p className="kb-meta">{record.title}</p>}
      {record.discredited && <span className="kb-pill">Discredited</span>}
      {record.superseded_by.length > 0 && (
        <p className="kb-superseded">
          Superseded by{" "}
          {record.superseded_by.map((next, index) => (
            <span key={next.id}>
              {index > 0 && ", "}
              <button type="button" className="kb-link-button" onClick={() => onSelect(next.id)}>
                {next.title}
              </button>
            </span>
          ))}
        </p>
      )}
      <div className="kb-body">
        {record.body ? (
          <MarkdownContent content={record.body} normalize={false} readerTitle={title} />
        ) : (
          <p className="kb-muted">This record has no body.</p>
        )}
      </div>
      {(record.ticket_id || record.versions.length > 0) && (
        <p className="kb-meta">
          {record.ticket_id && (
            <>
              From ticket{" "}
              <Link className="kb-link" to={ticketPath(record.ticket_id)}>
                {record.ticket_id}
              </Link>
            </>
          )}
          {record.ticket_id && record.versions.length > 0 && " · "}
          {record.versions.length > 0 &&
            `Revised ${record.versions.length} ${record.versions.length === 1 ? "time" : "times"}`}
        </p>
      )}
      {record.node_type === "learning" && (
        <>
          <h3 className="kb-section-title">Confidence</h3>
          <LearningConfidenceReadout confidence={record.confidence} ladder={record.ladder} />
        </>
      )}
      <h3 className="kb-section-title">Recorded relationships</h3>
      <Neighbours record={record} onSelect={onSelect} />
      <h3 className="kb-section-title">Shares a ticket, milestone or tag with</h3>
      <SharedWith nodeId={record.id} groups={groups} nodes={nodes} onSelect={onSelect} />
      <Origin record={record} />
    </>
  );
}

export function KnowledgePanel({
  nodeId,
  workspaceSlug,
  groups,
  nodes,
  onSelect,
  onClose,
}: {
  nodeId: string;
  workspaceSlug: string;
  /** The map's inferred groups and records, for "shares a … with". */
  groups: InferredGroup[];
  nodes: GraphNode[];
  onSelect: (nodeId: string) => void;
  onClose: () => void;
}) {
  const panelRef = useDialogFocusTrap<HTMLElement>();
  useDialogDismiss(onClose);
  const record = useQuery({
    queryKey: ["memory-node", workspaceSlug, nodeId],
    queryFn: () => api.memoryNode(nodeId, workspaceSlug),
    // A 404 is the "record not found" state below, not a failure to toast.
    meta: { errorTitle: "Load record", suppressErrorToast: true },
    retry: false,
  });
  const notFound = errorStatus(record.error) === 404;
  // The global query toast is suppressed above so a 404 reads as "not found";
  // every other failure is still reported, once per error.
  useEffect(() => {
    if (record.error && errorStatus(record.error) !== 404) {
      toastActionFailed("Load record", record.error);
    }
  }, [record.error]);

  let body;
  if (record.isLoading) {
    body = <PaneSkeleton variant="list" rows={6} label="Loading record…" />;
  } else if (record.data) {
    body = <Record record={record.data} groups={groups} nodes={nodes} onSelect={onSelect} />;
  } else if (notFound) {
    body = (
      <div className="kb-state" role="status">
        <h2 id="kb-panel-title" className="kb-panel-title">
          Record not found
        </h2>
        <p className="kb-muted">No record with this id exists in {workspaceSlug}.</p>
        <Link className="kb-link" to={memoryPath("map")}>
          Back to all records
        </Link>
      </div>
    );
  } else {
    body = (
      <div className="kb-state" role="alert">
        <h2 id="kb-panel-title" className="kb-panel-title">
          Could not load this record
        </h2>
        <p>{describeError(record.error, "The request failed")}</p>
        <button type="button" className="btn-secondary" onClick={() => void record.refetch()}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <aside
      ref={panelRef}
      className="kb-panel"
      role="dialog"
      aria-modal="false"
      aria-labelledby="kb-panel-title"
    >
      <div className="kb-panel-close">
        <IconCloseButton onClick={onClose} />
      </div>
      {body}
    </aside>
  );
}
