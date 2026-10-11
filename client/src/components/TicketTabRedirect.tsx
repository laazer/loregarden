import { Navigate, useParams } from "react-router-dom";

import { DEFAULT_ARTIFACT_TAB, ticketPath } from "../lib/appNavigation";

export function TicketTabRedirect() {
  const { ticketId } = useParams<{ ticketId: string }>();
  if (!ticketId) {
    return <Navigate to="/" replace />;
  }
  return <Navigate to={ticketPath(ticketId, DEFAULT_ARTIFACT_TAB)} replace />;
}
