import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

import { useTicketDetailsEditor } from "../hooks/useTicketDetailsEditor";
import { navigateToTicket } from "../lib/useAppNavigation";
import { useTicketPeekStore } from "../state/ticketPeekStore";
import { TicketDetailsModal } from "./TicketDetailsModal";

/**
 * The dialog `peekTicket` opens: the ticket's details, editable as from the
 * dashboard, with "Open ticket" to go on to its page. Mounted once, in
 * `AppLayout`. Any route change closes it, so a peek never outlives the page
 * it was opened over.
 */
export function TicketPeekHost() {
  const peek = useTicketPeekStore((s) => s.peek);
  const close = useTicketPeekStore((s) => s.close);
  const details = useTicketDetailsEditor(peek?.ticketId ?? null, peek !== null);
  const { clearSaveError } = details;
  const location = useLocation();
  const lastLocation = useRef(location.key);

  useEffect(() => {
    if (location.key === lastLocation.current) return;
    lastLocation.current = location.key;
    close();
  }, [location.key, close]);

  const dismiss = () => {
    close();
    clearSaveError();
  };

  return (
    <TicketDetailsModal
      ticket={details.ticket}
      isOpen={peek !== null}
      onClose={dismiss}
      isLoading={details.isLoading}
      error={details.loadError}
      isSaving={details.isSaving}
      saveError={details.saveError}
      onSave={details.save}
      onSaveChecks={details.saveChecks}
      isSavingChecks={details.isSavingChecks}
      onOpenTicket={() => {
        if (!peek) return;
        // Prefer the shareable id: it is the address the ticket page keeps.
        const id = details.ticket?.external_id || details.ticket?.id || peek.ticketId;
        dismiss();
        navigateToTicket(id, { tab: peek.tab });
      }}
    />
  );
}
