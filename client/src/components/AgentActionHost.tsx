import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { API_BASE } from "../api/client";
import { UI_ACTION_LABELS } from "../lib/agentActions/catalog";
import { uiActionRegistry } from "../lib/agentActions/registry";
import { UiActionSocket, uiActionSocketUrl } from "../lib/agentActions/uiActionSocket";
import { useAgentAction } from "../lib/agentActions/useAgentAction";
import { pathForPage, ticketPath } from "../lib/appNavigation";
import { pushToast } from "../state/toastStore";

/**
 * Lets agents drive this tab: connects it to `/ws/ui-actions` and offers the
 * actions that work from anywhere — showing a page, opening a ticket. Actions
 * tied to one surface register where that surface renders (`TicketAgentActions`).
 *
 * Every action an agent runs here is announced to the operator, success or
 * failure: an agent moving the screen without a word is the experience this
 * must never produce. Renders nothing itself.
 */
export function AgentActionHost() {
  const navigate = useNavigate();

  useAgentAction("navigate.page", async ({ page }) => {
    const path = pathForPage(page);
    if (!path) throw new Error(`no page named ${page}`);
    navigate(path);
    return { path };
  });

  useAgentAction("ticket.open", async ({ ticket_id }) => {
    // TicketRouteResolver turns an external id into the canonical UUID route,
    // and says so on the page when nothing answers to it.
    const path = ticketPath(ticket_id);
    navigate(path);
    return { path };
  });

  useEffect(() => {
    const socket = new UiActionSocket(uiActionSocketUrl(API_BASE), {
      // The connection itself is not the operator's concern: with it down, an
      // agent's invoke fails by name on the server side ("no tab connected").
      onStatus: () => {},
      onInvoke: (name, outcome) => {
        if (outcome.ok) {
          pushToast({ tone: "info", title: `Agent: ${UI_ACTION_LABELS[name]}` });
        } else {
          pushToast({
            tone: "warning",
            title: `Agent tried: ${UI_ACTION_LABELS[name]}`,
            message: outcome.error,
          });
        }
      },
    });
    socket.attach(uiActionRegistry);
    socket.open();
    const onVisibility = () => socket.reportVisibility(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
      socket.close();
    };
  }, []);

  return null;
}
