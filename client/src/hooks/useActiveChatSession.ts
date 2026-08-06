import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api, type Approval } from "../api/client";
import type { WorkspaceRuntimeSettings } from "../api/client";
import { ticketIdFromPath } from "../lib/appNavigation";
import type { ChatSession } from "../lib/chatSession";
import { useUiStore } from "../state/uiStore";
import { useBaxterChatSession } from "./useBaxterChatSession";
import { useBranchChatSession } from "./useBranchChatSession";
import { useChatWorkspaceSlug } from "./useChatWorkspace";
import { useTicketChatSession } from "./useTicketChatSession";
import { useTriageSession } from "./useTriageSession";

/**
 * The past conversations a bound chat can be swapped between, when it keeps
 * any.
 *
 * Only the Baxter thread has an archive: a ticket's triage conversation is the
 * ticket's, and a branch's is the branch's — there is no other one to open. So
 * this is null rather than a set of no-ops, and a surface offering "New chat"
 * or "History" shows the controls only when they would do something.
 */
export interface ChatArchive {
  workspaceSlug: string;
  /** The open thread's id, or "" for a fresh one not yet saved. */
  sessionId: string;
  openSession: (id: string) => void;
  startNewChat: () => void;
  runtime: WorkspaceRuntimeSettings;
  setRuntime: (runtime: WorkspaceRuntimeSettings) => Promise<void>;
  isSavingRuntime: boolean;
}

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
   * — so chat surfaces render these as a trailing in-thread ask rather than
   * as chrome layered over the transcript.
   */
  pendingApprovals: Approval[];
  /**
   * The branch this conversation's work sits on, when it has one.
   *
   * Read so the surfaces can offer branch-only actions — shipping a change, for
   * one — without each of them working out where the branch comes from.
   */
  branch: string | null;
  /** Past threads of this conversation, or null when it keeps none. */
  archive: ChatArchive | null;
  /**
   * The screen is this conversation's own surface and composes for it.
   *
   * Distinct from simply having no session: there is a conversation, the page
   * is showing it, and the bar's chat half would only duplicate the composer
   * already on screen. Surfaces read this to hide those controls rather than
   * disable them.
   */
  composedOnScreen: boolean;
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
  // `/chat` composes for this thread itself, and Home's hero is the way into
  // the same one; a second composer for the same conversation would only open
  // the dock on top of the page already showing it.
  const composedOnScreen =
    pathname === "/" || pathname === "/chat" || pathname.startsWith("/chat/");
  const chatWorkspaceSlug = useChatWorkspaceSlug();
  // Bound on the chat page too, though the composer half stays hidden there: the
  // model picker lives in the bar on every screen, and it is the same thread.
  const baxterSession = useBaxterChatSession(
    !onBranchTriage && !ticketId ? chatWorkspaceSlug : "",
  );
  const baxterArchive: ChatArchive | null = chatWorkspaceSlug
    ? {
        workspaceSlug: chatWorkspaceSlug,
        sessionId: baxterSession.sessionId,
        openSession: baxterSession.openSession,
        startNewChat: baxterSession.startNewChat,
        runtime: baxterSession.runtime,
        setRuntime: baxterSession.setRuntime,
        isSavingRuntime: baxterSession.isSavingRuntime,
      }
    : null;

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

  const none = {
    session: null,
    label: "",
    ticketId: null,
    pendingApprovals: [],
    branch: null,
    archive: null,
    composedOnScreen: false,
  };
  // Home and `/chat` are the same conversation's own surfaces. Binding nothing
  // there is not enough on its own: the bar would still offer a dead composer
  // reading "open a ticket or a branch", which is wrong twice over — there is a
  // conversation, and it is right there on the page.
  // The archive still rides along: the bar owns the model picker everywhere, so
  // the chat page reads it from here rather than drawing its own.
  if (composedOnScreen) return { ...none, composedOnScreen: true, archive: baxterArchive };
  if (onBranchTriage) {
    return branch
      ? {
          session: branchSession,
          label: `Branch · ${branch}`,
          ticketId: null,
          pendingApprovals: [],
          branch,
          archive: null,
          composedOnScreen: false,
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
      archive: null,
      composedOnScreen: false,
    };
  }
  if (!chatWorkspaceSlug) return none;
  return {
    session: baxterSession,
    label: `Baxter · ${chatWorkspaceSlug}`,
    ticketId: null,
    pendingApprovals: baxterSession.pendingApprovals,
    branch: null,
    composedOnScreen: false,
    archive: baxterArchive,
  };
}
