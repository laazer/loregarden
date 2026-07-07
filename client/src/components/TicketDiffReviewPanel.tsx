import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import {
  addRunOutputComment,
  ensureRunOutputReview,
  getQueueOperationDiff,
  getRunOutputReview,
  listQueueOperations,
  type QueueOperationDetails,
  type RunOutputReviewData,
} from "../lib/queueReviewApi";
import { OperationDiffReviewView } from "./OperationDiffReviewView";
import { RunOutputReview } from "./RunOutputReview";
import "./TicketDiffReviewPanel.css";

interface TicketRun {
  id: string;
  run_code: string;
  status: string;
  stdout?: string;
  stderr?: string;
}

export function TicketDiffReviewPanel({
  workspaceId,
  ticketId,
  runs,
}: {
  workspaceId: string;
  ticketId: string;
  runs: TicketRun[];
}) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(runs[0]?.id ?? null);
  const [outputType, setOutputType] = useState<"stdout" | "stderr">("stdout");
  const [outputReview, setOutputReview] = useState<RunOutputReviewData | null>(null);
  const [operation, setOperation] = useState<QueueOperationDetails | null>(null);
  const [runOutputById, setRunOutputById] = useState<
    Record<string, { stdout?: string; stderr?: string; run_code?: string }>
  >({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"output" | "operation">("output");

  const ticketRunIds = useMemo(() => new Set(runs.map((run) => run.id)), [runs]);

  useEffect(() => {
    if (!selectedRunId && runs[0]?.id) {
      setSelectedRunId(runs[0].id);
    }
  }, [runs, selectedRunId]);

  const loadOutputReview = useCallback(async () => {
    if (!selectedRunId) return;
    setIsLoading(true);
    setError(null);
    try {
      let run = runs.find((item) => item.id === selectedRunId);
      if (!run?.stdout && !run?.stderr) {
        const full = await api.run(selectedRunId);
        run = { ...run, ...full, id: full.id, run_code: full.run_code, status: full.status };
      }
      const content = outputType === "stdout" ? run?.stdout ?? "" : run?.stderr ?? "";
      const review = await ensureRunOutputReview(workspaceId, selectedRunId, outputType, content);
      setOutputReview(review);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load output review");
      setOutputReview(null);
    } finally {
      setIsLoading(false);
    }
  }, [outputType, runs, selectedRunId, workspaceId]);

  const loadLatestOperation = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const listed = await listQueueOperations(workspaceId, { limit: 20 });
      const match = listed.operations.find((op) => op.affected_count > 0) ?? listed.operations[0];
      if (!match) {
        setOperation(null);
        return;
      }
      const details = await getQueueOperationDiff(workspaceId, match.id);
      const relevant = (details.diff ?? []).some((change) => ticketRunIds.has(change.run_id));
      if (!relevant && details.affected_run_ids?.every((id) => !ticketRunIds.has(id))) {
        setOperation(null);
        return;
      }
      setOperation(details);

      const runIds = [
        ...new Set([
          ...(details.affected_run_ids ?? []),
          ...(details.diff ?? []).map((change) => change.run_id),
        ].filter((id) => ticketRunIds.has(id))),
      ];
      const outputs: Record<string, { stdout?: string; stderr?: string; run_code?: string }> = {};
      await Promise.all(
        runIds.map(async (runId) => {
          const run = await api.run(runId);
          outputs[runId] = { stdout: run.stdout, stderr: run.stderr, run_code: run.run_code };
        }),
      );
      setRunOutputById(outputs);
      setMode("operation");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load queue operation review");
    } finally {
      setIsLoading(false);
    }
  }, [ticketRunIds, workspaceId]);

  useEffect(() => {
    if (mode === "output") {
      void loadOutputReview();
    }
  }, [mode, loadOutputReview]);

  const handleAddLineComment = async (lineNumber: number, content: string) => {
    if (!selectedRunId || !outputReview) return;
    setIsLoading(true);
    setError(null);
    try {
      await addRunOutputComment(workspaceId, selectedRunId, outputReview.review_id, lineNumber, content);
      const refreshed = await getRunOutputReview(workspaceId, selectedRunId, outputReview.review_id);
      setOutputReview(refreshed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add comment");
    } finally {
      setIsLoading(false);
    }
  };

  if (!workspaceId) {
    return null;
  }

  return (
    <section className="ticket-diff-review-panel">
      <div className="ticket-diff-review-header">
        <div>
          <div className="state-label">Review</div>
          <div className="ticket-diff-review-title">GitHub-style review on this ticket</div>
        </div>
        <div className="ticket-diff-review-tabs">
          <button
            type="button"
            className={`btn-secondary btn-compact ${mode === "output" ? "active" : ""}`}
            onClick={() => setMode("output")}
          >
            Run output
          </button>
          <button
            type="button"
            className={`btn-secondary btn-compact ${mode === "operation" ? "active" : ""}`}
            onClick={() => void loadLatestOperation()}
          >
            Queue operation
          </button>
        </div>
      </div>

      {error ? <div className="ticket-diff-review-error">{error}</div> : null}

      {mode === "operation" && operation ? (
        <OperationDiffReviewView
          workspaceId={workspaceId}
          operation={operation}
          runOutputById={runOutputById}
          onRefresh={async () => {
            const refreshed = await getQueueOperationDiff(workspaceId, operation.operation_id);
            setOperation(refreshed);
          }}
        />
      ) : mode === "operation" ? (
        <div className="ticket-diff-review-loading">
          {isLoading ? "Loading queue operation review…" : "No queue operations found for this ticket yet."}
        </div>
      ) : !runs.length ? (
        <div className="ticket-diff-review-loading">
          Run this ticket to capture stdout/stderr for line-by-line review.
        </div>
      ) : (
        <div className="ticket-diff-review-output">
          <div className="ticket-diff-review-toolbar">
            <label className="ticket-diff-review-label">
              Run
              <select
                className="btn-secondary filter-select"
                value={selectedRunId ?? ""}
                onChange={(e) => setSelectedRunId(e.target.value)}
              >
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.run_code} · {run.status}
                  </option>
                ))}
              </select>
            </label>
            <div className="ticket-diff-review-output-types">
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
            <div className="ticket-diff-review-loading">
              {isLoading ? "Loading run output…" : "No output available for review."}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
