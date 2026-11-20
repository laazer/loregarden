import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { OperatorQuestionRow } from "../../../api/operatorQuestionApi";
import { useApprovalResolution } from "../../../hooks/useApprovalResolution";
import { useTicketOperatorQuestions } from "../../../hooks/useTicketOperatorQuestions";
import { describeError } from "../../../state/toastStore";
import { ApprovalCard, type ApprovalResolvePayload } from "../../ApprovalCard";
import { PrimitiveCard } from "./PrimitiveCard";
import type { OperatorQuestionPart } from "./types";

/** What became of a question that is no longer open, in the operator's words. */
function settledLine(row: OperatorQuestionRow): string {
  if (row.outcome === "overridden" && row.queued_behind)
    return `Overridden: ${row.answer}. ${row.affects} re-runs with it once ${row.queued_behind} finishes.`;
  if (row.outcome === "overridden") return `Overridden: ${row.answer}. ${row.affects} re-runs with it.`;
  if (row.outcome === "confirmed") return `Confirmed: ${row.answer || row.default}.`;
  return `Ignored: the agent's default '${row.default}' stood.`;
}

/**
 * An agent's question, with the answer the operator gave Baxter staged on it.
 *
 * Baxter cannot answer for the operator (resolving an approval is theirs), so
 * this is the inbox card itself with their answer preselected; their press is
 * what records it. Once settled, the card says what was decided.
 */
export function OperatorQuestionPrimitive({ part }: { part: OperatorQuestionPart }) {
  const feed = useTicketOperatorQuestions(part.ticket_id);
  const approvals = useQuery({
    queryKey: ["approvals", part.ticket_id],
    queryFn: () => api.approvals(part.ticket_id),
    refetchInterval: 4000,
  });
  const resolve = useApprovalResolution(part.ticket_id);

  const row = feed.data?.questions.find((question) => question.id === part.approval_id);
  const approval = approvals.data?.find((candidate) => candidate.id === part.approval_id);
  const loading = feed.isLoading || (row?.status === "pending" && approvals.isLoading);
  const failed = feed.error ?? approvals.error;
  const missing = !loading && !failed && feed.data && !row;
  const open = row?.status === "pending";

  return (
    <PrimitiveCard
      kind="operator_question"
      tint={open ? "you" : "mute"}
      title={part.title || row?.question || "An agent's question"}
      refLine={feed.data?.ticket ?? part.ticket_id}
      loading={loading}
      error={
        failed
          ? describeError(failed, "Could not load this question")
          : missing
            ? `No question ${part.approval_id} on ${part.ticket_id}. Ask Baxter to list the open ones again.`
            : null
      }
    >
      {open && approval ? (
        <>
          <ApprovalCard
            approval={approval}
            stagedAnswer={part.answer ?? undefined}
            isSubmitting={resolve.isPending}
            onApprove={(payload?: ApprovalResolvePayload) =>
              resolve.mutate({ id: approval.id, action: "approve", ...payload })
            }
            onReject={(payload?: ApprovalResolvePayload) =>
              resolve.mutate({ id: approval.id, action: "reject", ...payload })
            }
            onIgnore={(reason: string) =>
              resolve.mutate({ id: approval.id, action: "ignore", response: reason })
            }
          />
          {resolve.isError ? (
            <p className="lg-primitive-card-error" role="alert">
              {describeError(resolve.error, "Could not record your answer")}
            </p>
          ) : null}
        </>
      ) : null}
      {row && !open ? <p className="lg-primitive-card-sub">{settledLine(row)}</p> : null}
    </PrimitiveCard>
  );
}
