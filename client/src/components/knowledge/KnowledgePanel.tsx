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
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";

import { api } from "../../api/client";
import type { MemoryNodeDetail } from "../../api/memoryApi";
import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";
import { NODE_TYPE_LABELS } from "../../lib/knowledgeLayout";
import { ticketPath } from "../../lib/appNavigation";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { describeError, errorStatus, toastActionFailed } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { PaneSkeleton } from "../ui/PaneSkeleton";

const ORIGIN_LABELS = { agent: "Agent", human: "Human", import: "Import" } as const;

function Origin({ record }: { record: MemoryNodeDetail }) {
  const recorded = formatLocalTimestamp(record.created_at);
  const parts = record.origin_kind
    ? [ORIGIN_LABELS[record.origin_kind], record.origin_ref ?? "reference not recorded", recorded]
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

function Record({
  record,
  onSelect,
}: {
  record: MemoryNodeDetail;
  onSelect: (nodeId: string) => void;
}) {
  return (
    <>
      <div className="state-label">{NODE_TYPE_LABELS[record.node_type]}</div>
      <h2 id="kb-panel-title" className="kb-panel-title">
        {record.title}
      </h2>
      {record.discredited && <span className="kb-pill">Discredited</span>}
      <div className="kb-body">
        {record.body ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{record.body}</ReactMarkdown>
        ) : (
          <p className="kb-muted">This record has no body.</p>
        )}
      </div>
      {record.ticket_id && (
        <p>
          <Link to={ticketPath(record.ticket_id)}>Ticket {record.ticket_id}</Link>
        </p>
      )}
      <h3 className="kb-section-title">Related</h3>
      <Neighbours record={record} onSelect={onSelect} />
      <Origin record={record} />
    </>
  );
}

export function KnowledgePanel({
  nodeId,
  workspaceSlug,
  onSelect,
  onClose,
}: {
  nodeId: string;
  workspaceSlug: string;
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
    body = <Record record={record.data} onSelect={onSelect} />;
  } else if (notFound) {
    body = (
      <div className="kb-state" role="status">
        <h2 id="kb-panel-title" className="kb-panel-title">
          Record not found
        </h2>
        <p className="kb-muted">No record with this id exists in {workspaceSlug}.</p>
        <Link to="/knowledge">Back to all records</Link>
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
