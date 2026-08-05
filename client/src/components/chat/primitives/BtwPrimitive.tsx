import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { MarkdownContent } from "../MarkdownContent";
import { PrimitiveCard } from "./PrimitiveCard";
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
  // worse than one that appears a moment late.
  const refusal = interactive
    ? (live?.escalation_refusal ?? "Checking whether the run can be asked…")
    : "Preview — this card is not bound to a real aside.";

  const escalate = useMutation({
    meta: { errorTitle: "Ask the running agent" },
    mutationFn: () => api.escalateAside(ticketId, part.exchange_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-asides", ticketId] });
    },
  });

  const answer = live?.answer || part.answer || "";
  const status = live?.status ?? (answer ? "answered" : "pending");
  const observed = part.observed_agent_id
    ? `${part.observed_agent_id}${part.observed_stage_key ? ` · ${part.observed_stage_key}` : ""}`
    : "";

  const attribution = observed
    ? `Read from ${observed}'s log by Baxter — not answered by that agent.`
    : "Answered from the record by Baxter.";

  return (
    <PrimitiveCard
      title={part.title ?? "Aside"}
      subtitle={part.question}
      tone={status === "failed" ? "warn" : "default"}
      meta={<span>{attribution}</span>}
      error={status === "failed" ? live?.error || "This aside was never answered." : null}
      actions={
        escalated ? (
          <span className="lg-primitive-card-sub">
            Also put to the running agent — its reply is in that run's log.
          </span>
        ) : refusal ? (
          <span className="lg-primitive-card-sub">{refusal}</span>
        ) : (
          <button
            type="button"
            className="lg-primitive-run-btn"
            disabled={escalate.isPending}
            title={
              "Writes this question into the running agent's input. It will enter " +
              "that agent's context and can change what it does next."
            }
            onClick={() => escalate.mutate()}
          >
            {escalate.isPending ? "Asking…" : "Ask the running agent (affects its run)"}
          </button>
        )
      }
    >
      {status === "pending" ? (
        <p className="lg-primitive-card-sub">Reading the run's log…</p>
      ) : answer ? (
        <MarkdownContent content={answer} />
      ) : null}
    </PrimitiveCard>
  );
}
