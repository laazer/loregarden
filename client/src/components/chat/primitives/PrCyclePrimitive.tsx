import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { mergeQueueApi, mergeQueueKeys } from "../../../api/mergeQueueApi";
import { describeError } from "../../../state/toastStore";
import { Button } from "../../ui/Button";
import { VineNode } from "../../vine/VineNode";
import { PrimitiveCard } from "./PrimitiveCard";
import { planPrCycle, prCycleIsMoving, type PrCycleAction } from "./prCycle";
import type { PrCyclePart } from "./types";
import "./PrCyclePrimitive.css";

/** Each re-read is a `gh` call, so only while a stage is moving on its own. */
const MOVING_REFETCH_MS = 15_000;

function ActionButton({
  action,
  busy,
  repairSent,
  canRepair,
  onRepair,
  onEnqueue,
}: {
  action: PrCycleAction;
  busy: boolean;
  repairSent: boolean;
  canRepair: boolean;
  onRepair: (prompt: string) => void;
  onEnqueue: (prNumber: number) => void;
}) {
  if (action.kind === "repair") {
    return (
      <Button
        variant="primary"
        compact
        disabled={repairSent || !canRepair}
        title={canRepair ? undefined : "Repair asks this thread's agent; open the card in its chat"}
        onClick={() => onRepair(action.prompt)}
      >
        {repairSent ? "Repair requested" : "Repair"}
      </Button>
    );
  }
  if (action.kind === "enqueue") {
    return (
      <Button variant="primary" compact disabled={busy} onClick={() => onEnqueue(action.prNumber)}>
        {busy ? "Enqueuing…" : "Enqueue PR"}
      </Button>
    );
  }
  return (
    <Button variant="secondary" compact disabled>
      {action.label}
    </Button>
  );
}

/**
 * A branch from push to base: pre-push checks, PR checks, merge queue, with
 * one button for the next move (Repair, Enqueue PR, or disabled while it waits).
 */
export function PrCyclePrimitive({
  part,
  onSubmit,
}: {
  part: PrCyclePart;
  onSubmit?: (content: string) => void;
}) {
  const prNumber = part.pr_number ?? null;
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: mergeQueueKeys.prCycle(part.workspace_slug, part.branch, prNumber),
    queryFn: () => mergeQueueApi.prCycle(part.workspace_slug, part.branch, prNumber),
    // A failed pre-push stops the cycle before the PR: no GitHub call to make.
    enabled: part.pre_push !== "failed",
    refetchInterval: (q) =>
      q.state.data && prCycleIsMoving(planPrCycle(part, q.state.data)) ? MOVING_REFETCH_MS : false,
  });
  // Keyed by the prompt: once the card moves to a different failure, Repair is offered again.
  const [repairSentFor, setRepairSentFor] = useState<string | null>(null);
  // Set in the click, before React re-renders: `isPending` alone lets a fast
  // second click through, and a second admission is a second queue entry.
  const enqueueing = useRef(false);
  const enqueue = useMutation({
    meta: { errorTitle: "Enqueue PR" },
    mutationFn: (number: number) => mergeQueueApi.enqueuePullRequest(part.workspace_slug, number),
    onSettled: () => {
      enqueueing.current = false;
      void qc.invalidateQueries({ queryKey: mergeQueueKeys.all });
    },
  });

  const plan = planPrCycle(part, query.data);
  const read = query.data?.pull_request;
  const pr = read?.lookup === "found" ? read.pull_request : null;
  const readError = query.error
    ? `Couldn't read this branch's PR: ${describeError(query.error, "request failed")}`
    : read?.lookup === "failed"
      ? `GitHub didn't answer for this branch's PR: ${read.error}`
      : null;
  const repairSent = plan.action.kind === "repair" && repairSentFor === plan.action.prompt;

  return (
    <PrimitiveCard
      kind="pr_cycle"
      title={part.title ?? pr?.title ?? part.branch}
      refLine={pr ? `#${pr.number} · ${part.branch} → ${pr.base}` : `${part.workspace_slug} · ${part.branch}`}
      copyRef={part.branch}
      status={query.isLoading ? null : plan.status}
      loading={query.isLoading}
      skeletonLines={3}
      error={readError}
      footerHint={
        enqueue.isError ? (
          <span role="alert">{describeError(enqueue.error, "The queue refused the PR")}</span>
        ) : repairSent ? (
          "Sent to the agent in this thread"
        ) : (
          plan.action.hint
        )
      }
      actions={
        query.isLoading ? null : (
          <ActionButton
            action={plan.action}
            busy={enqueue.isPending}
            repairSent={repairSent}
            canRepair={Boolean(onSubmit)}
            onRepair={(prompt) => {
              setRepairSentFor(prompt);
              onSubmit?.(prompt);
            }}
            onEnqueue={(number) => {
              if (enqueueing.current) return;
              enqueueing.current = true;
              enqueue.mutate(number);
            }}
          />
        )
      }
    >
      <ol className="lg-primitive-pr-cycle" aria-label="Way to the base">
        {/* ux-ok: planPrCycle always returns the same three stages; these are not fetched rows */}
        {plan.stages.map((stage, index) => (
          <li key={stage.key} data-mark={stage.mark}>
            <VineNode index={index} mark={stage.mark} last={index === plan.stages.length - 1} />
            <span className="lg-primitive-pr-cycle-label">{stage.label}</span>
            <span className="lg-primitive-pr-cycle-detail" title={stage.detail}>
              {stage.detail}
            </span>
          </li>
        ))}
      </ol>
    </PrimitiveCard>
  );
}
