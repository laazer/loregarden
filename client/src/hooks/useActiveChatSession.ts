import { useLocation } from "react-router-dom";

import { ticketIdFromPath } from "../lib/appNavigation";
import type { ChatSession } from "../lib/chatSession";
import { useUiStore } from "../state/uiStore";
import { useBranchChatSession } from "./useBranchChatSession";
import { useTicketChatSession } from "./useTicketChatSession";

export interface ActiveChatSession {
  session: ChatSession | null;
  /** What the dock calls the bound conversation, e.g. "Ticket triage". */
  label: string;
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
 */
export function useActiveChatSession(): ActiveChatSession {
  const { pathname } = useLocation();
  const branchWorkspace = useUiStore((s) => s.branchTriageWorkspaceSlug);
  const branch = useUiStore((s) => s.branchTriageBranch);

  const onBranchTriage = pathname.startsWith("/branch-triage");
  const ticketId = onBranchTriage ? null : ticketIdFromPath(pathname);

  const ticketSession = useTicketChatSession(ticketId ?? undefined);
  const branchSession = useBranchChatSession(
    onBranchTriage ? branchWorkspace : "",
    onBranchTriage ? branch : "",
  );

  if (onBranchTriage) {
    return branch
      ? { session: branchSession, label: `Branch · ${branch}` }
      : { session: null, label: "" };
  }
  return ticketId
    ? { session: ticketSession, label: "Ticket triage" }
    : { session: null, label: "" };
}
