import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api, type Approval } from "../api/client";
import { ticketIdFromPath } from "../lib/appNavigation";
import type { ChatSession } from "../lib/chatSession";
import { useUiStore } from "../state/uiStore";
import { useBaxterChatSession } from "./useBaxterChatSession";
import { useBranchChatSession } from "./useBranchChatSession";
import { useChatWorkspaceSlug } from "./useChatWorkspace";
import { useTicketChatSession } from "./useTicketChatSession";
import { useTriageSession } from "./useTriageSession";

export interface ActiveChatSession {
  session: ChatSession | null;
  /** What the dock calls the bound conversation, e.g. "Ticket triage". */
  label: string;
  /**
   * The ticket this conversation belongs to, when it has one.
   *
   * Approvals are a ticket concept, so this is null for branch triage. Kept
   * out of `ChatSession` for that reason: a field only some implementers can
   * fill gets answered with a no-op by the rest.
   */
  ticketId: string | null;
  /**
   * Decisions waiting on the operator in this conversation.
   *
   * An agent question never arrives as a chat message — it becomes an approval
   * — so a surface showing only messages leaves the agent apparently working
   * with nothing to answer.
   */
  pendingApprovals: Approval[];
  /**
   * The branch this conversation's work sits on, when it has one.
   *
   * Read so the surfaces can offer branch-only actions — shipping a change, for
   * one — without each of them working out where the branch comes from.
   */
  branch: string | null;
}

/**
 * Whichever conversation the current screen is showing, or none.
 *
 * The dock is a utility that opens the chat already on screen rather than a
 * separate assistant, so the binding is derived from the route and never
 * stored: a remembered binding would survive navigation and leave the dock
 * talking to a conversation the screen is no longer showing.
 *
 * Both session hooks are called unconditionally — hooks cannot be called in a
 * branch — which is safe because each guards its queries on having an id, so
 * the inactive one issues no requests.
 *
 * The ticket id is read from the path rather than `useParams`: this runs above
 * `<Routes>`, where no route has matched and params are always empty.
 *
 * A screen that owns no conversation of its own — Studio, Queue, the Console —
 * still gets one: the workspace's Baxter thread, the same thread `/chat` shows.
 * The bar is dead weight otherwise, and there is nothing screen-specific about
 * asking Baxter a question.
 */
export function useActiveChatSession(): ActiveChatSession {
  const { pathname } = useLocation();
  const branchWorkspace = useUiStore((s) => s.branchTriageWorkspaceSlug);
  const branch = useUiStore((s) => s.branchTriageBranch);

  const onBranchTriage = pathname.startsWith("/branch-triage");
  const ticketId = onBranchTriage ? null : ticketIdFromPath(pathname);
  // `/chat` composes for this thread itself; a second composer for the same
  // conversation would only open the dock on top of the page showing it.
  const onBaxterChat = pathname === "/chat" || pathname.startsWith("/chat/");
  const chatWorkspaceSlug = useChatWorkspaceSlug();
  const baxterSession = useBaxterChatSession(
    !onBranchTriage && !ticketId && !onBaxterChat ? chatWorkspaceSlug : "",
  );

  const ticketSession = useTicketChatSession(ticketId ?? undefined);
  const { pending } = useTriageSession(ticketId ?? undefined);
  const branchSession = useBranchChatSession(
    onBranchTriage ? branchWorkspace : "",
    onBranchTriage ? branch : "",
  );

  // Same key the action bar and the dashboard use, so the ticket's branch costs
  // no extra request.
  const { data: ticket } = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => api.ticket(ticketId as string),
    enabled: Boolean(ticketId),
  });

  const none = { session: null, label: "", ticketId: null, pendingApprovals: [], branch: null };
  if (onBranchTriage) {
    return branch
      ? {
          session: branchSession,
          label: `Branch · ${branch}`,
          ticketId: null,
          pendingApprovals: [],
          branch,
        }
      : none;
  }
  if (ticketId) {
    return {
      session: ticketSession,
      label: "Ticket triage",
      ticketId,
      pendingApprovals: pending,
      branch: ticket?.branch || null,
    };
  }
  if (onBaxterChat || !chatWorkspaceSlug) return none;
  return {
    session: baxterSession,
    label: `Baxter · ${chatWorkspaceSlug}`,
    ticketId: null,
    pendingApprovals: [],
    branch: null,
  };
}
