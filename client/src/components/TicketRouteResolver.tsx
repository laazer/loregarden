import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect } from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";

import { api } from "../api/client";
import { navigateToPage } from "../lib/useAppNavigation";
import { looksLikeTicketUuid } from "../lib/ticketIds";
import { useTicketRefStore } from "../state/ticketRefStore";
import { describeError } from "../state/toastStore";

/** Keep a ticket route on the ticket's shareable id, and tell the page its UUID.
 *
 * `/tickets/lor-mcp-gateway-142/diff` is the address someone pastes into a
 * message, so it is the one the address bar shows. The page's dozen queries are
 * keyed by the UUID instead; rather than teach each of them both spellings, the
 * pair is learned once here and recorded in `useTicketRefStore`, which
 * `useTicketIdFromRoute` reads to hand everything below the UUID.
 *
 * Two ways in:
 * - **A shareable id** (pasted, or a link built after the pair was learned):
 *   resolved once, then the page renders with the URL left as it is.
 * - **A UUID** (a link built from a run or queue row before the pair was
 *   learned, or an old bookmark): the page renders straight away, and the URL is
 *   swapped for the shareable one when the ticket arrives. A ticket with no
 *   shareable id stays on its UUID.
 *
 * Every rewrite replaces rather than pushes: Back should leave the ticket, not
 * land on the other spelling and bounce forward again.
 */
export function TicketRouteResolver({ children }: { children: ReactNode }) {
  const { ticketId } = useParams<{ ticketId: string }>();
  const location = useLocation();
  const queryClient = useQueryClient();
  const ref = ticketId ?? "";
  const isUuid = looksLikeTicketUuid(ref);
  const uuid = useTicketRefStore((s) => (isUuid ? ref : s.uuidByRef[ref]));
  const shareableId = useTicketRefStore((s) => (isUuid ? s.refByUuid[ref] : ref));
  const remember = useTicketRefStore((s) => s.remember);

  const { data, error, isLoading } = useQuery({
    // The UUID spelling shares the page's own key, so the page's fetch and this
    // one are the same request.
    queryKey: isUuid ? ["ticket", ref] : ["ticket-ref", ref],
    queryFn: () => api.ticket(ref),
    enabled: ref !== "" && (isUuid ? !shareableId : !uuid),
    retry: false,
  });

  useEffect(() => {
    if (!data) return;
    remember(data);
    // Same payload the page is about to ask for under the UUID: hand it over.
    if (!isUuid) queryClient.setQueryData(["ticket", data.id], data);
  }, [data, isUuid, queryClient, remember]);

  // Rebuilt from the path rather than from a tab constant, so any deeper
  // segment a future route adds survives the rewrite untouched.
  const rewriteTo = (id: string) => {
    const rest = location.pathname.split("/").slice(3).join("/");
    const suffix = rest ? `/${rest}` : "";
    return (
      <Navigate to={`/tickets/${encodeURIComponent(id)}${suffix}${location.search}${location.hash}`} replace />
    );
  };

  if (!isUuid && ref !== "" && !uuid) {
    if (isLoading) return null;
    if (!data) return <TicketNotFound ticketRef={ref} error={error} />;
    // A pre-restructure id resolves to a ticket that now answers to another
    // one: move to the current spelling, which is the one being recorded.
    const canonical = data.external_id || data.id;
    if (canonical !== ref) return rewriteTo(canonical);
    // Between the answer arriving and the effect recording it.
    return null;
  }

  // A UUID address with a known shareable id is swapped for it. The page stays
  // mounted through the swap — the same children in the same slot on both
  // spellings — so the address changes and nothing reloads.
  const redirect = isUuid && shareableId ? rewriteTo(shareableId) : null;
  return (
    <>
      {redirect}
      {children}
    </>
  );
}

function TicketNotFound({ ticketRef, error }: { ticketRef: string; error: unknown }) {
  return (
    <div className="queue-page-empty">
      <h2 style={{ marginTop: 0 }}>No ticket with that id</h2>
      <p style={{ maxWidth: 520 }}>
        Nothing in this control plane answers to <code>{ticketRef}</code>
        {error ? ` — ${describeError(error, "the lookup failed")}` : "."}
      </p>
      <button type="button" className="btn-secondary" onClick={() => navigateToPage("home")}>
        Back to Home
      </button>
    </div>
  );
}
