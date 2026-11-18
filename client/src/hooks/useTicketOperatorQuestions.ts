import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";

/** Every question agents asked the operator on a ticket, open and settled. */
export function useTicketOperatorQuestions(ticketId: string | undefined) {
  return useQuery({
    queryKey: ["operator-questions", ticketId],
    queryFn: () => api.ticketOperatorQuestions(ticketId as string),
    refetchInterval: 15000,
    enabled: !!ticketId,
  });
}
