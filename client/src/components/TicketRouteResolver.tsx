import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect } from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";

import { api } from "../api/client";
import { navigateToPage } from "../lib/useAppNavigation";
import { looksLikeTicketUuid } from "../lib/ticketIds";

/** Let a shareable ticket id stand in for the UUID in a ticket route.
 *
 * `/tickets/lor-mcp-gateway-142/diff` is the id someone pastes into a message;
 * `/tickets/<uuid>/diff` is what the page's dozen queries are keyed by. Rather
 * than teach each of those to accept both, the ref is resolved once here and the
 * URL is rewritten to the canonical form, so everything below this point sees
 * only UUIDs and the address bar ends up showing the same link for everyone.
 *
 * The redirect replaces rather than pushes: a Back press should leave the
 * ticket, not land on the ref and bounce forward again.
 */
export function TicketRouteResolver({ children }: { children: ReactNode }) {
  const { ticketId } = useParams<{ ticketId: string }>();
  const location = useLocation();
  const queryClient = useQueryClient();
  const ref = ticketId ?? "";
  const needsResolving = ref !== "" && !looksLikeTicketUuid(ref);

  const { data, error, isLoading } = useQuery({
    queryKey: ["ticket-ref", ref],
    queryFn: () => api.ticket(ref),
    enabled: needsResolving,
    retry: false,
  });

  // The response is the same payload the canonical route is about to ask for,
  // so hand it over rather than making the redirect fetch it a second time.
  useEffect(() => {
    if (data) queryClient.setQueryData(["ticket", data.id], data);
  }, [data, queryClient]);

  if (!needsResolving) return <>{children}</>;

  if (data) {
    // Rebuilt from the path rather than from a tab constant, so any deeper
    // segment a future route adds survives the rewrite untouched.
    const rest = location.pathname.split("/").slice(3).join("/");
    const suffix = rest ? `/${rest}` : "";
    return (
      <Navigate to={`/tickets/${data.id}${suffix}${location.search}${location.hash}`} replace />
    );
  }

  if (isLoading) return null;

  return (
    <div className="queue-page-empty">
      <h2 style={{ marginTop: 0 }}>No ticket with that id</h2>
      <p style={{ maxWidth: 520 }}>
        Nothing in this control plane answers to <code>{ref}</code>
        {error instanceof Error && error.message ? ` — ${error.message}` : "."}
      </p>
      <button type="button" className="btn-secondary" onClick={() => navigateToPage("home")}>
        Back to Home
      </button>
    </div>
  );
}
