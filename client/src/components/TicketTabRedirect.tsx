import { Navigate, useParams } from "react-router-dom";

import { ticketPath } from "../lib/appNavigation";

export function TicketTabRedirect() {
  const { ticketId } = useParams<{ ticketId: string }>();
  if (!ticketId) {
    return <Navigate to="/" replace />;
  }
  return <Navigate to={ticketPath(ticketId, "diff")} replace />;
}
