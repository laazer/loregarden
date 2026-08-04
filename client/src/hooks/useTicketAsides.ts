import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type BtwExchange } from "../api/client";
import type { ChatMessageView } from "../components/chat/chatUtils";
import type { BtwPart } from "../components/chat/primitives/types";

/**
 * A ticket's asides, and the ability to raise one.
 *
 * An answered aside is mirrored into the triage transcript by the server, so
 * the thread already has it. A pending or failed one has no message — the
 * mirror is written when the answer lands — which is exactly the stretch the
 * operator most needs to see something for. `asideMessages` synthesises those
 * two states as chat messages so every surface rendering the thread shows them
 * without knowing asides exist.
 */
export function useTicketAsides(ticketId: string | undefined) {
  const qc = useQueryClient();

  const asides = useQuery({
    queryKey: ["ticket-asides", ticketId],
    queryFn: () => api.ticketAsides(ticketId!),
    enabled: Boolean(ticketId),
    refetchInterval: (query) => {
      const pending = (query.state.data?.exchanges ?? []).some(
        (item) => item.status === "pending",
      );
      // Nothing in flight means nothing will change without an action of the
      // operator's own, which invalidates this key anyway.
      return pending ? 2000 : false;
    },
  });

  const ask = useMutation({
    meta: { errorTitle: "Ask an aside" },
    mutationFn: (content: string) => api.askAside(ticketId!, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-asides", ticketId] });
    },
  });

  const exchanges = asides.data?.exchanges ?? [];
  return {
    exchanges,
    asideMessages: exchanges.filter((item) => item.status !== "answered").map(asideMessage),
    ask,
    isAsking: ask.isPending,
    askError: ask.isError ? (ask.error as Error)?.message || "Could not ask" : null,
  };
}

function asidePart(exchange: BtwExchange): BtwPart {
  return {
    primitive: "btw",
    exchange_id: exchange.id,
    ticket_id: exchange.ticket_id,
    question: exchange.question,
    answer: exchange.answer,
    observed_run_id: exchange.observed_run_id,
    observed_agent_id: exchange.observed_agent_id,
    observed_stage_key: exchange.observed_stage_key,
    escalated: exchange.escalated,
  };
}

function asideMessage(exchange: BtwExchange): ChatMessageView {
  return {
    id: `btw-${exchange.id}`,
    role: "assistant",
    // The card carries the question and the answer; the message body exists for
    // surfaces that render text only.
    content: exchange.question,
    created_at: exchange.created_at,
    parts: [asidePart(exchange)],
  };
}
