import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useLocation } from "react-router-dom";

import { reviewApi } from "../api/reviewApi";
import { pageFromPath } from "../lib/appNavigation";
import { selectReview } from "../lib/reviewSelection";
import { useTicketIdFromPath } from "../lib/useAppNavigation";
import { REVIEW_QUEUE_KEY } from "./useReviewLaunch";

/** The review queue, narrowed to one workspace when one is named. */
export function useReviewQueue(workspace: string, enabled = true) {
  return useQuery({
    queryKey: [...REVIEW_QUEUE_KEY, workspace],
    queryFn: () => reviewApi.queue(workspace || undefined),
    meta: { suppressErrorToast: true },
    enabled,
  });
}

export interface ScreenTicket {
  /** The UUID of the ticket the screen is showing, or null when it shows none. */
  ticketId: string | null;
  /**
   * The screen shows a ticket, but which one is not known yet. Chrome must not
   * fall back to some other conversation or workspace meanwhile: a message sent
   * in that gap would go somewhere the operator is not looking.
   */
  resolving: boolean;
}

/**
 * The ticket on screen, for chrome that sits outside the route: the one in a
 * ticket page's path, or the one the review page is on.
 *
 * The review page keeps its place in the query string, so the path alone says
 * nothing there. Same query key as the page, so asking again costs no request.
 */
export function useScreenTicket(): ScreenTicket {
  const { pathname, search } = useLocation();
  const pathTicketId = useTicketIdFromPath();
  const onReview = pageFromPath(pathname) === "review";
  const params = useMemo(() => new URLSearchParams(search), [search]);
  const queue = useReviewQueue(params.get("workspace") ?? "", onReview);
  if (!onReview) return { ticketId: pathTicketId, resolving: false };
  return {
    ticketId: selectReview(queue.data?.builds ?? [], params).item?.ticket.id ?? null,
    resolving: queue.isPending,
  };
}
