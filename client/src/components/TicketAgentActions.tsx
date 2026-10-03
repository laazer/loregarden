import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { api } from "../api/client";
import { useAgentAction } from "../lib/agentActions/useAgentAction";
import { DEFAULT_RUNTIME } from "../lib/runtimeSettings";
import { useRunControls } from "./chat/primitives/useRunControls";

/**
 * The open ticket's write actions, offered to agents while a ticket route is
 * showing — and only then, so an agent cannot edit a ticket the operator is
 * not looking at. Each goes through the same `api.updateTicket` call and the
 * same cache refresh as the ticket modal's own save.
 *
 * The server gates these as writes before this tab is ever asked. Renders
 * nothing itself.
 */
export function TicketAgentActions() {
  const { ticketId = "" } = useParams<{ ticketId: string }>();
  const queryClient = useQueryClient();
  const { data: ticket } = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => api.ticket(ticketId),
    enabled: ticketId !== "",
  });

  /** The agent names a ticket; refuse unless it is the one on screen. */
  const requireOpen = (requested: string): string => {
    if (!ticket) throw new Error("the ticket is still loading; try again");
    if (requested !== ticket.id && requested !== ticket.external_id) {
      throw new Error(
        `${requested} is not the open ticket (${ticket.external_id || ticket.id}); open it first with ticket.open`,
      );
    }
    return ticket.id;
  };

  const refresh = (id: string) => {
    void queryClient.invalidateQueries({ queryKey: ["ticket", id] });
    void queryClient.invalidateQueries({ queryKey: ["ticket-tree"] });
    void queryClient.invalidateQueries({ queryKey: ["tickets"] });
  };

  useAgentAction(
    "ticket.update",
    async ({ ticket_id, ...fields }) => {
      const id = requireOpen(ticket_id);
      if (Object.keys(fields).length === 0) throw new Error("no fields to change");
      const updated = await api.updateTicket(id, fields);
      refresh(id);
      return { ticket_id: updated.id, title: updated.title, priority: updated.priority };
    },
    ticketId !== "",
  );

  useAgentAction(
    "ticket.set_state",
    async ({ ticket_id, state }) => {
      const id = requireOpen(ticket_id);
      const updated = await api.updateTicket(id, { state });
      refresh(id);
      return { ticket_id: updated.id, state: updated.state };
    },
    ticketId !== "",
  );

  useAgentAction(
    "ticket.trigger_auto_fix",
    async ({ ticket_id }) => {
      const id = requireOpen(ticket_id);
      // Straight to the API, not through useAutoFix: that hook keeps a failure in
      // component state, which would report success to the agent.
      return { ticket_id: id, ...(await api.triggerAutoFix(id)) };
    },
    ticketId !== "",
  );

  const runControls = useRunControls(ticketId || undefined);

  useAgentAction(
    "ticket.start_stage",
    async ({ ticket_id, stage_key }) => {
      const id = requireOpen(ticket_id);
      if (!ticket?.stages.some((stage) => stage.key === stage_key)) {
        throw new Error(`${ticket?.external_id || id} has no stage ${stage_key}`);
      }
      const started = await runControls.start(stage_key);
      // Admission may park the run rather than start it: say which.
      return { ticket_id: id, stage_key, admission: started?.admission ?? null };
    },
    ticketId !== "",
  );

  useAgentAction(
    "ticket.stop",
    async ({ ticket_id }) => {
      const id = requireOpen(ticket_id);
      await runControls.stop();
      return { ticket_id: id, stopped: true };
    },
    ticketId !== "",
  );

  useAgentAction(
    "ticket.set_runtime",
    async ({ ticket_id, ...change }) => {
      const id = requireOpen(ticket_id);
      if (Object.keys(change).length === 0) throw new Error("no runtime fields to change");
      const saved = await api.setTicketRuntime(id, { ...(ticket?.orchestration_runtime ?? DEFAULT_RUNTIME), ...change });
      void queryClient.invalidateQueries({ queryKey: ["ticket", id] });
      return saved;
    },
    ticketId !== "",
  );

  return null;
}
