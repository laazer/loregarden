import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { navigateToTicketTab } from "../../../lib/useAppNavigation";
import { describeError } from "../../../state/toastStore";
import { MarkdownContent } from "../MarkdownContent";
import { Button } from "../../ui/Button";
import { PrimitiveCard } from "./PrimitiveCard";
import { OverflowMenuItem } from "../../OverflowMenu";
import type { BtwPart } from "./types";

/**
 * An aside — something asked while a run was working — and its answer.
 *
 * The card's job is to keep two things apart that look identical once they are
 * both text in a thread: what the working agent did, and what a read-only
 * observer inferred about it afterwards. Every answer here is the observer's,
 * and the attribution line says so in words rather than leaving it to be
 * deduced from the card's colour.
 *
 * Live state is refetched rather than read from the stored part: whether the
 * observed run can still be asked directly is a fact about now, and the part was
 * written when the answer landed.
 *
 * `interactive: false` turns that off — the card renders from the part alone and
 * offers no escalation. That is what the gallery uses: its exchange id refers to
 * nothing, so a fetch would hang the card on "checking…" forever and a button
 * would post to an aside that does not exist.
 */
export function BtwPrimitive({ part }: { part: BtwPart }) {
  const qc = useQueryClient();
  const ticketId = part.ticket_id;
  const interactive = part.interactive !== false;

  const asides = useQuery({
    queryKey: ["ticket-asides", ticketId],
    queryFn: () => api.ticketAsides(ticketId),
    enabled: interactive && Boolean(ticketId),
  });

  const live = asides.data?.exchanges.find((item) => item.id === part.exchange_id);
  const escalated = live?.escalated ?? part.escalated ?? false;
  // Absent live data, offer nothing: a button that turns out to be refused is
  // worse than one that appears a moment late. A lookup that *failed*, though,
  // must say so — "checking…" that never resolves is a dead card claiming to be
  // a live one, and the operator would wait on it.
  const lookupFailed = interactive && asides.isError;
  const refusal = !interactive
    ? "Preview — this card is not bound to a real aside."
    : lookupFailed
      ? describeError(asides.error, "Could not check whether the run can be asked")
      : (live?.escalation_refusal ?? "Checking whether the run can be asked…");

  const escalate = useMutation({
    meta: { errorTitle: "Ask the running agent" },
    mutationFn: () => api.escalateAside(ticketId, part.exchange_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-asides", ticketId] });
    },
  });

  // Both keys, because an aside is rendered from two places: an answered one
  // from the triage transcript the server mirrored it into, a pending or failed
  // one synthesised from the aside list itself. Invalidating one would clear the
  // card on some surfaces and leave it on others.
  const dismiss = useMutation({
    meta: { errorTitle: "Dismiss this aside" },
    mutationFn: () => api.deleteAside(ticketId, part.exchange_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-asides", ticketId] });
      qc.invalidateQueries({ queryKey: ["triage", ticketId] });
    },
  });

  const answer = live?.answer || part.answer || "";
  const status = live?.status ?? (answer ? "answered" : "pending");
  const observedRun = live?.observed_run_id ?? part.observed_run_id ?? null;
  const observed = part.observed_agent_id
    ? `${part.observed_agent_id}${part.observed_stage_key ? ` · ${part.observed_stage_key}` : ""}`
    : "";

  const attribution = observed
    ? `Read from ${observed}'s log by Baxter — not answered by that agent.`
    : "Answered from the record by Baxter.";

  // One action at the foot (asking the running agent), with what it costs as
  // the hint; deleting the card is secondary, in ⋯.
  const canAsk = !escalated && !refusal;
  const footerHint = escalated
    ? "Also put to the running agent — its reply is in that run's log."
    : refusal
      ? refusal
      : "Asking writes into its input and can change what it does next.";

  return (
    <PrimitiveCard
      kind="btw"
      title={part.title ?? "Aside"}
      refLine={observed ? `from ${observed}` : null}
      status={status === "failed" ? { tone: "bad", label: "Unanswered" } : null}
      error={status === "failed" ? live?.error || "This aside was never answered." : null}
      footerHint={footerHint}
      actions={
        canAsk ? (
          <Button
            variant="secondary"
            compact
            disabled={escalate.isPending || dismiss.isPending}
            // The cost on the control itself too, for whoever reaches it first.
            title={
              "Writes this question into the running agent's input. It will enter " +
              "that agent's context and can change what it does next."
            }
            onClick={() => escalate.mutate()}
          >
            {escalate.isPending ? "Asking…" : "Ask the running agent"}
          </Button>
        ) : null
      }
      menu={
        // Always offered, escalated or not: dismissing is about this card, not
        // the question, and an aside already put to the run is the one most
        // likely to be done with.
        interactive && ticketId ? (
          <OverflowMenuItem
            danger
            disabled={dismiss.isPending}
            title={
              escalated
                ? "Takes the card off this thread. The running agent has already been asked and that cannot be taken back."
                : "Takes the card off this thread."
            }
            onSelect={() => dismiss.mutate()}
          >
            {dismiss.isPending ? "Deleting…" : "Delete this aside"}
          </OverflowMenuItem>
        ) : null
      }
    >
      <blockquote className="lg-btw-question">{part.question}</blockquote>
      {status === "pending" ? (
        <p className="lg-primitive-card-sub">Reading the run's log…</p>
      ) : answer ? (
        <MarkdownContent content={answer} />
      ) : null}
      <p className="lg-btw-attribution">
        {attribution}
        {/* The card asserts what the log said; this is how that gets checked. */}
        {interactive && observedRun && ticketId ? (
          <>
            {" "}
            <Button
              variant="plain"
              className="lg-btw-log-link"
              onClick={() => navigateToTicketTab(ticketId, "timeline")}
            >
              Open the run timeline
            </Button>
          </>
        ) : null}
      </p>
    </PrimitiveCard>
  );
}
