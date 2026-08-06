import { API_BASE } from "../api/client";
import type { OperationComment } from "../components/QueueOperationReview";
import type { OutputLine } from "../components/RunOutputReview";
import type { DiffChange } from "../components/QueueDiffViewer";

export interface QueueOperationSummary {
  id: string;
  workspace_id: string;
  operation_type: string;
  description: string;
  created_by: string;
  created_at: string;
  approved: boolean;
  executed: boolean;
  affected_count: number;
}

export interface QueueOperationDetails {
  operation_id: string;
  workspace_id: string;
  operation_type: string;
  description: string;
  created_by: string;
  created_at: string;
  before_state: unknown[];
  after_state: unknown[];
  diff: DiffChange[];
  affected_run_ids: string[];
  comments: OperationComment[];
  approved: boolean;
  approved_by?: string;
  approved_at?: string | null;
}

export interface RunOutputReviewData {
  review_id: string;
  run_id: string;
  output_type: "stdout" | "stderr";
  lines: OutputLine[];
  total_comments: number;
  approved?: boolean;
  approved_by?: string;
}

async function reviewRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export async function listQueueOperations(
  workspaceIdOrOptions?: string | { workspaceId?: string; approvedOnly?: boolean; limit?: number },
  options?: { approvedOnly?: boolean; limit?: number },
): Promise<{ operations: QueueOperationSummary[]; total: number }> {
  const opts =
    typeof workspaceIdOrOptions === "string"
      ? { workspaceId: workspaceIdOrOptions, ...options }
      : (workspaceIdOrOptions ?? {});
  const params = new URLSearchParams({
    approved_only: String(opts.approvedOnly ?? false),
    limit: String(opts.limit ?? 20),
  });
  const path = opts.workspaceId
    ? `/api/parallel/workspace/${opts.workspaceId}/queue/operations?${params}`
    : `/api/parallel/queue/operations?${params}`;
  return reviewRequest(path);
}

export async function getQueueOperationDiff(
  workspaceId: string,
  operationId: string,
): Promise<QueueOperationDetails> {
  return reviewRequest(
    `/api/parallel/workspace/${workspaceId}/queue/operations/${operationId}/diff`,
  );
}

export async function addQueueOperationComment(
  workspaceId: string,
  operationId: string,
  body: { content: string; run_id?: string; line_number?: number; created_by?: string },
): Promise<void> {
  await reviewRequest(
    `/api/parallel/workspace/${workspaceId}/queue/operations/${operationId}/comment`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function approveQueueOperation(
  workspaceId: string,
  operationId: string,
  approvedBy = "operator",
): Promise<void> {
  await reviewRequest(
    `/api/parallel/workspace/${workspaceId}/queue/operations/${operationId}/approve`,
    { method: "POST", body: JSON.stringify({ approved_by: approvedBy }) },
  );
}

export async function submitQueueOperationToAgent(
  workspaceId: string,
  operationId: string,
  body: { agent_id: string; instructions?: string; approved_by?: string },
): Promise<void> {
  await reviewRequest(
    `/api/parallel/workspace/${workspaceId}/queue/operations/${operationId}/submit-to-agent`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function ensureRunOutputReview(
  workspaceId: string,
  runId: string,
  outputType: "stdout" | "stderr",
  outputContent: string,
): Promise<RunOutputReviewData> {
  const created = await reviewRequest<{ review_id: string }>(
    `/api/parallel/workspace/${workspaceId}/runs/${runId}/output-review`,
    {
      method: "POST",
      body: JSON.stringify({ output_type: outputType, output_content: outputContent }),
    },
  );
  return getRunOutputReview(workspaceId, runId, created.review_id);
}

export async function getRunOutputReview(
  workspaceId: string,
  runId: string,
  reviewId: string,
): Promise<RunOutputReviewData> {
  return reviewRequest(
    `/api/parallel/workspace/${workspaceId}/runs/${runId}/output-review/${reviewId}`,
  );
}

export async function addRunOutputComment(
  workspaceId: string,
  runId: string,
  reviewId: string,
  lineNumber: number,
  content: string,
): Promise<void> {
  await reviewRequest(
    `/api/parallel/workspace/${workspaceId}/runs/${runId}/output-review/${reviewId}/comment`,
    { method: "POST", body: JSON.stringify({ line_number: lineNumber, content }) },
  );
}
