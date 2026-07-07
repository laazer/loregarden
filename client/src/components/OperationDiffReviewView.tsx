import { useCallback, useEffect, useMemo, useState } from "react";

import {
  addQueueOperationComment,
  addRunOutputComment,
  approveQueueOperation,
  ensureRunOutputReview,
  getRunOutputReview,
  submitQueueOperationToAgent,
  type QueueOperationDetails,
  type RunOutputReviewData,
} from "../lib/queueReviewApi";
import { QueueDiffViewer } from "./QueueDiffViewer";
import { QueueOperationReview } from "./QueueOperationReview";
import { RunOutputReview } from "./RunOutputReview";
import "./OperationDiffReviewView.css";

export interface OperationDiffReviewViewProps {
  workspaceId: string;
  operation: QueueOperationDetails;
  runOutputById?: Record<string, { stdout?: string; stderr?: string; run_code?: string }>;
  onRefresh: () => Promise<void>;
}

type ReviewPanelTab = "diff" | "output";

export function OperationDiffReviewView({
  workspaceId,
  operation,
  runOutputById = {},
  onRefresh,
}: OperationDiffReviewViewProps) {
  const [panelTab, setPanelTab] = useState<ReviewPanelTab>("diff");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [outputType, setOutputType] = useState<"stdout" | "stderr">("stdout");
  const [outputReview, setOutputReview] = useState<RunOutputReviewData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const affectedRunIds = useMemo(() => {
    const fromDiff = (operation.diff ?? []).map((c) => c.run_id);
    const fromMeta = operation.affected_run_ids ?? [];
    return [...new Set([...fromDiff, ...fromMeta].filter(Boolean))];
  }, [operation]);

  const loadOutputReview = useCallback(
    async (runId: string, type: "stdout" | "stderr") => {
      setIsLoading(true);
      setError(null);
      try {
        const run = runOutputById[runId];
        const content = type === "stdout" ? run?.stdout ?? "" : run?.stderr ?? "";
        const review = await ensureRunOutputReview(workspaceId, runId, type, content);
        setOutputReview(review);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load run output review");
        setOutputReview(null);
      } finally {
        setIsLoading(false);
      }
    },
    [runOutputById, workspaceId],
  );

  useEffect(() => {
    if (panelTab === "output" && selectedRunId) {
      void loadOutputReview(selectedRunId, outputType);
    }
  }, [panelTab, selectedRunId, outputType, loadOutputReview]);

  const handleAddComment = async (content: string, runId?: string, lineNumber?: number) => {
    setIsLoading(true);
    setError(null);
    try {
      await addQueueOperationComment(workspaceId, operation.operation_id, {
        content,
        run_id: runId,
        line_number: lineNumber,
      });
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add comment");
    } finally {
      setIsLoading(false);
    }
  };

  const handleApprove = async () => {
    setIsLoading(true);
    setError(null);
    try {
      await approveQueueOperation(workspaceId, operation.operation_id);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to approve");
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmitToAgent = async (agentId: string, instructions?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      await submitQueueOperationToAgent(workspaceId, operation.operation_id, {
        agent_id: agentId,
        instructions: instructions ?? "",
      });
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit to agent");
    } finally {
      setIsLoading(false);
    }
  };

  const handleReviewRunOutput = (runId: string) => {
    setSelectedRunId(runId);
    setPanelTab("output");
  };

  const handleAddLineComment = async (lineNumber: number, content: string) => {
    if (!selectedRunId || !outputReview) return;
    setIsLoading(true);
    setError(null);
    try {
      await addRunOutputComment(workspaceId, selectedRunId, outputReview.review_id, lineNumber, content);
      const refreshed = await getRunOutputReview(workspaceId, selectedRunId, outputReview.review_id);
      setOutputReview(refreshed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add line comment");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="operation-diff-review">
      <div className="operation-diff-review-tabs">
        <button
          type="button"
          className={`operation-diff-review-tab ${panelTab === "diff" ? "active" : ""}`}
          onClick={() => setPanelTab("diff")}
        >
          Diff & comments
        </button>
        <button
          type="button"
          className={`operation-diff-review-tab ${panelTab === "output" ? "active" : ""}`}
          onClick={() => setPanelTab("output")}
          disabled={!selectedRunId}
        >
          Run output
          {selectedRunId ? ` · ${runOutputById[selectedRunId]?.run_code ?? selectedRunId.slice(0, 8)}` : ""}
        </button>
      </div>

      {error ? <div className="operation-diff-review-error">{error}</div> : null}

      {panelTab === "diff" ? (
        <>
          <QueueDiffViewer
            beforeState={operation.before_state || []}
            afterState={operation.after_state || []}
            changes={operation.diff || []}
            operationType={operation.operation_type}
            description={operation.description}
            comments={operation.comments || []}
            onAddComment={handleAddComment}
            onReviewRunOutput={handleReviewRunOutput}
            isLoading={isLoading}
          />
          <QueueOperationReview
            operationId={operation.operation_id}
            comments={operation.comments || []}
            approved={operation.approved}
            approvedBy={operation.approved_by}
            onAddComment={handleAddComment}
            onApprove={handleApprove}
            onSubmitToAgent={handleSubmitToAgent}
            isLoading={isLoading}
          />
        </>
      ) : selectedRunId ? (
        <div className="operation-diff-review-output">
          <div className="operation-diff-review-output-toolbar">
            <label className="operation-diff-review-output-label">
              Run
              <select
                className="btn-secondary filter-select"
                value={selectedRunId}
                onChange={(e) => setSelectedRunId(e.target.value)}
              >
                {affectedRunIds.map((runId) => (
                  <option key={runId} value={runId}>
                    {runOutputById[runId]?.run_code ?? runId}
                  </option>
                ))}
              </select>
            </label>
            <div className="operation-diff-review-output-types">
              <button
                type="button"
                className={`btn-secondary btn-compact ${outputType === "stdout" ? "active" : ""}`}
                onClick={() => setOutputType("stdout")}
              >
                STDOUT
              </button>
              <button
                type="button"
                className={`btn-secondary btn-compact ${outputType === "stderr" ? "active" : ""}`}
                onClick={() => setOutputType("stderr")}
              >
                STDERR
              </button>
            </div>
            <button type="button" className="btn-secondary btn-compact" onClick={() => setPanelTab("diff")}>
              ← Back to diff
            </button>
          </div>
          {outputReview ? (
            <RunOutputReview
              outputType={outputReview.output_type || outputType}
              lines={outputReview.lines || []}
              approved={outputReview.approved}
              approvedBy={outputReview.approved_by}
              onAddComment={handleAddLineComment}
              isLoading={isLoading}
            />
          ) : (
            <div className="operation-diff-review-loading">
              {isLoading ? "Loading run output…" : "No output captured for this run."}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
