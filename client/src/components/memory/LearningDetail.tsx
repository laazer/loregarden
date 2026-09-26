/**
 * One learning in full: its names, body, confidence, relations, lineage and
 * history, and the control that discredits or restores it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { MemoryNode } from "../../api/memoryApi";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { describeError, pushToast } from "../../state/toastStore";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { DiscreditConfirmModal } from "./DiscreditConfirmModal";
import { LearningConfidenceReadout } from "./LearningConfidenceReadout";
import { LearningLineage } from "./LearningLineage";
import { LearningRelations } from "./LearningRelations";

export function LearningDetail({
  node,
  workspaceSlug,
  onOpen,
}: {
  node: MemoryNode;
  workspaceSlug: string;
  onOpen: (nodeId: string) => void;
}) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const detail = useQuery({
    queryKey: ["memory-node", workspaceSlug, node.id],
    queryFn: () => api.memoryNode(node.id, workspaceSlug),
    meta: { errorTitle: "Load learning" },
  });
  const current = detail.data ?? node;

  const change = useMutation({
    meta: { errorTitle: current.discredited ? "Restore learning" : "Discredit learning" },
    mutationFn: (reason: string) =>
      api.setMemoryNodeDiscredited(node.id, {
        workspace_slug: workspaceSlug,
        discredited: !current.discredited,
        reason,
      }),
    onSuccess: (updated) => {
      qc.setQueryData(["memory-node", workspaceSlug, node.id], updated);
      void qc.invalidateQueries({ queryKey: ["memory-nodes", workspaceSlug] });
      void qc.invalidateQueries({ queryKey: ["memory-proposals", workspaceSlug] });
      setConfirming(false);
      pushToast({
        tone: "success",
        title: updated.discredited ? "Learning discredited" : "Learning restored",
        message: updated.title,
      });
    },
  });

  const lastChange = detail.data?.versions.at(-1);
  const inLineage = detail.data?.relations.some((edge) => edge.relation_type === "supersedes");

  return (
    <section className="memory-detail" aria-labelledby="memory-detail-title">
      <header className="memory-detail-header">
        <div>
          <h3 id="memory-detail-title" className="memory-detail-title">
            {current.title}
          </h3>
          {current.discredited && (
            <span className="state-label memory-discredited-label">discredited</span>
          )}
        </div>
        <button
          type="button"
          className={current.discredited ? "btn-secondary" : "btn-primary memory-danger"}
          disabled={change.isPending}
          onClick={() => setConfirming(true)}
        >
          {change.isPending ? "Saving…" : current.discredited ? "Restore" : "Discredit"}
        </button>
      </header>
      {current.aliases.length > 0 && (
        <p className="memory-muted">Also known as {current.aliases.join(", ")}</p>
      )}
      {detail.data && detail.data.superseded_by.length > 0 && (
        <p className="memory-superseded" role="note">
          Superseded by {detail.data.superseded_by.map((s) => s.title).join("; ")}. Agents still see
          it, labelled and ranked after its replacement.
        </p>
      )}
      <p className="memory-detail-body">{current.body || "(no body)"}</p>
      {detail.isLoading ? (
        <PaneSkeleton variant="list" rows={3} label="Loading confidence and history…" />
      ) : detail.data ? (
        <>
          <h4 className="memory-section-title">Observed outcomes</h4>
          <LearningConfidenceReadout
            confidence={detail.data.confidence}
            ladder={detail.data.ladder}
          />
          <h4 className="memory-section-title">Relations</h4>
          <LearningRelations relations={detail.data.relations} onOpen={onOpen} />
          {inLineage && (
            <>
              <h4 className="memory-section-title">What changed</h4>
              <LearningLineage nodeId={node.id} workspaceSlug={workspaceSlug} />
            </>
          )}
          <h4 className="memory-section-title">Last change</h4>
          <p className="memory-muted">
            {lastChange
              ? `${formatLocalTimestamp(lastChange.superseded_at)} by ${lastChange.superseded_by ?? "an unrecorded writer"}${
                  lastChange.change_note ? ` — ${lastChange.change_note}` : ""
                }`
              : "Never changed since it was written."}
          </p>
        </>
      ) : (
        <p className="memory-error" role="alert">
          Could not load this learning's history:{" "}
          {describeError(detail.error, "the request failed")}.
        </p>
      )}
      <DiscreditConfirmModal
        key={confirming ? node.id : "closed"}
        node={confirming ? current : null}
        isSaving={change.isPending}
        onClose={() => setConfirming(false)}
        onConfirm={(reason) => change.mutate(reason)}
      />
    </section>
  );
}
