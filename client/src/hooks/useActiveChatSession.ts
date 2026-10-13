import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api, type Approval } from "../api/client";
import type { WorkspaceRuntimeSettings } from "../api/client";
import { initiativeIdFromPath, ticketUuidForRef } from "../lib/appNavigation";
import { chatScopeInitiativeId, chatScopeWorkspaceSlug } from "../lib/chatScope";
import { useTicketIdFromPath } from "../lib/useAppNavigation";
import type { ChatMessageView } from "../components/chat/chatUtils";
import type { ChatSession } from "../lib/chatSession";
import { INITIATIVE_PLANNING, INITIATIVE_TRIAGE } from "../lib/dockChatPrompts";
import { chatPath } from "../lib/homeBaxter";
import { useInitiativeAgentStore } from "../state/initiativeAgentStore";
import { useTicketRefStore } from "../state/ticketRefStore";
import { useUiStore } from "../state/uiStore";
import { useBaxterChatSession } from "./useBaxterChatSession";
import { useBranchChatSession } from "./useBranchChatSession";
import { chatScopeLabel, useChatScopeKey } from "./useChatScope";
import { useInitiativeBaxterSession } from "./useInitiativeBaxterSession";
import { planQueryKey, useInitiativePlanner } from "./useInitiativePlanner";
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
  /** The archive this thread is in: a chat scope key (`lib/chatScope`). */
  scope: string;
  /** The open thread's id, or "" for a fresh one not yet saved. */
  sessionId: string;
  openSession: (id: string) => void;
  startNewChat: () => void;
  /** Create a fresh thread and send into it — what a note's "new chat" does. */
  sendInNewChat: (content: string) => Promise<unknown>;
  /**
   * Branch the open thread into a copy. Optional `body` is sent as the first
   * new turn on the fork. No-op when there is no session id yet.
   */
  forkSession: (body?: string) => Promise<unknown>;
  /**
   * Branch from one turn, dropping everything after it. The per-turn Fork
   * action in the thread; `/fork` still copies the whole conversation.
   */
  forkFromMessage: (messageId: string) => Promise<unknown>;
}

/**
 * The provider and model the bound conversation's next turn runs on.
 *
 * Separate from the archive because the two do not travel together: the
 * initiative planner keeps one conversation per initiative — nothing to swap
 * between — yet its model is as much the operator's choice as Baxter's.
 */
export interface ChatModelBinding {
  /** Who answers, as the picker's tooltip names them: "Baxter", "the initiative planner". */
  assistant: string;
  /** The model dialog's eyebrow: "Baxter", "Initiative planner". */
  scopeLabel: string;
  /** Whose runtime options (LM Studio server, effective defaults) apply; "" for global. */
  workspaceSlug: string;
  runtime: WorkspaceRuntimeSettings;
  setRuntime: (runtime: WorkspaceRuntimeSettings) => Promise<void>;
  isSavingRuntime: boolean;
  /** A turn is running; changing the model under it would not affect it. */
  isBusy: boolean;
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
  /** The bound conversation's model picker, or null when its model is not the operator's to pick. */
  model: ChatModelBinding | null;
  /**
   * The screen is this conversation's own surface and composes for it.
   *
   * Distinct from simply having no session: there is a conversation, the page
   * is showing it, and the bar's chat half would only duplicate the composer
   * already on screen. Surfaces read this to hide those controls rather than
   * disable them.
   */
  composedOnScreen: boolean;
  /**
   * The suggestions for the conversation a `composedOnScreen` page composes
   * for, or null.
   *
   * `session` stays null there so nothing draws a second composer or thread
   * for it; this is read only for what the bar still offers on those screens —
   * its suggestions, which are one click rather than a composer.
   */
  screenPrompts: ScreenPrompts | null;
}

/**
 * What the bar needs to offer one-click prompts into a conversation the page
 * composes for itself.
 *
 * Narrower than `ChatSession` because the initiative planner is not one — it
 * has no approvals, no chat mode — yet its page wants suggestions as much as
 * Home does.
 */
export interface ScreenPrompts {
  /** Which openers apply: a `TRY_ASKING` key in `lib/dockChatPrompts`. */
  kind: string;
  messages: ChatMessageView[];
  isBusy: boolean;
  /** A send would fail or go nowhere: the thread did not load, or there is nothing to plan. */
  unavailable: boolean;
  /**
   * The page that draws the reply, when it is not the one composing: a pick
   * there is taken to it. Null when the reply lands on the page itself.
   */
  replyPath: string | null;
  /** Failures toast through the send mutation's `meta.errorTitle`. */
  send: (prompt: string) => void;
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
  const pathTicketId = useTicketIdFromPath();
  const ticketId = onBranchTriage ? null : pathTicketId;
  // `/chat` composes for this thread itself, and Home's hero is the way into
  // the same one; a second composer for the same conversation would only open
  // the dock on top of the page already showing it.
  const composedOnScreen =
    pathname === "/" || pathname === "/chat" || pathname.startsWith("/chat/");
  // An initiative's plan page docks its own agent panel — the planner, or
  // Baxter focused on the initiative — beside the schedule. Neither is the
  // workspace Baxter thread, so the bar does not bind that one there: a second
  // composer under the panel's would be asking someone else.
  const initiativeId = initiativeIdFromPath(pathname);
  const plannerOnScreen = initiativeId !== null;
  const initiativeAgent = useInitiativeAgentStore((s) => s.agent);
  const chatScope = useChatScopeKey();
  // Only a focused chat's label needs the list; the key is the picker's, so it is cached.
  const focusedInitiative = chatScopeInitiativeId(chatScope);
  const { data: initiatives } = useQuery({
    queryKey: ["initiatives"],
    queryFn: api.initiatives,
    enabled: focusedInitiative !== null,
  });
  // Bound on the chat page too, though the composer half stays hidden there: the
  // model picker lives in the bar on every screen, and it is the same thread.
  const baxterSession = useBaxterChatSession(
    !onBranchTriage && !ticketId && !plannerOnScreen ? chatScope : "",
  );
  const baxterArchive: ChatArchive | null = chatScope
    ? {
        scope: chatScope,
        sessionId: baxterSession.sessionId,
        openSession: baxterSession.openSession,
        startNewChat: baxterSession.startNewChat,
        sendInNewChat: baxterSession.sendInNewChat,
        forkSession: baxterSession.forkSession,
        forkFromMessage: baxterSession.forkFromMessage,
      }
    : null;
  const baxterModel: ChatModelBinding | null = chatScope
    ? {
        assistant: "Baxter",
        scopeLabel: "Baxter",
        // A global thread runs on its host's runtime, known once the thread exists.
        workspaceSlug:
          chatScopeWorkspaceSlug(chatScope) || baxterSession.snapshot?.host_workspace_slug || "",
        runtime: baxterSession.runtime,
        setRuntime: baxterSession.setRuntime,
        isSavingRuntime: baxterSession.isSavingRuntime,
        isBusy: baxterSession.isBusy,
      }
    : null;
  // The path carries the shareable ref; the plan page and its panels key
  // everything by the UUID, so resolve it the way the route does (null until
  // the route learns it). Asking by the ref 404s.
  const initiativeUuid = useTicketRefStore((s) =>
    initiativeId ? ticketUuidForRef(initiativeId, s.uuidByRef) : null,
  );
  // Same query key as the plan page's planner panel, so binding it here costs
  // no request of its own; an empty id issues none off the plan page.
  const planner = useInitiativePlanner(initiativeUuid ?? "");
  const plannerModel: ChatModelBinding = {
    assistant: "the initiative planner",
    scopeLabel: "Initiative planner",
    workspaceSlug: planner.workspaceSlug,
    runtime: planner.runtime,
    setRuntime: planner.setRuntime,
    isSavingRuntime: planner.isSavingRuntime,
    isBusy: planner.isBusy,
  };
  // Same query as the plan page's, so it costs no request of its own: the
  // planner has nothing to schedule until a milestone is attached, and its
  // panel disables its composer until then.
  const plan = useQuery({
    queryKey: planQueryKey(initiativeUuid ?? ""),
    queryFn: () => api.initiativePlan(initiativeUuid as string),
    enabled: Boolean(initiativeUuid) && initiativeAgent === "planner",
  });
  const plannerPrompts: ScreenPrompts = {
    kind: INITIATIVE_PLANNING,
    messages: planner.messages,
    isBusy: planner.isBusy,
    unavailable:
      !initiativeUuid || Boolean(planner.loadError) || !plan.data?.milestones.length,
    replyPath: null,
    send: (prompt) => planner.send(prompt),
  };
  // Same query key as the panel's Baxter tab; bound only while it is the one shown.
  const initiativeBaxter = useInitiativeBaxterSession(
    initiativeAgent === "baxter" ? initiativeUuid ?? "" : "",
  );
  const initiativeBaxterModel: ChatModelBinding = {
    assistant: "Baxter",
    scopeLabel: "Baxter · initiative",
    // A global thread runs on its host's runtime, known once the thread exists.
    workspaceSlug: initiativeBaxter.snapshot?.host_workspace_slug || "",
    runtime: initiativeBaxter.runtime,
    setRuntime: initiativeBaxter.setRuntime,
    isSavingRuntime: initiativeBaxter.isSavingRuntime,
    isBusy: initiativeBaxter.isBusy,
  };
  const initiativeBaxterPrompts: ScreenPrompts = {
    kind: INITIATIVE_TRIAGE,
    messages: initiativeBaxter.messages,
    isBusy: initiativeBaxter.isBusy,
    unavailable: !initiativeUuid || initiativeBaxter.loadError,
    replyPath: null,
    send: (prompt) => {
      initiativeBaxter.send(prompt).catch(() => {
        // silent-ok: send is a mutation carrying meta.errorTitle, so the global
        // MutationCache toast fires and the panel renders chat.error.
      });
    },
  };

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
    model: null,
    composedOnScreen: false,
    screenPrompts: null,
  };
  // Home and `/chat` are the same conversation's own surfaces. Binding nothing
  // there is not enough on its own: the bar would still offer a dead composer
  // reading "open a ticket or a branch", which is wrong twice over — there is a
  // conversation, and it is right there on the page.
  // The archive and model still ride along: the bar owns the model picker
  // everywhere, so the chat page reads it from here rather than drawing its own.
  if (composedOnScreen) {
    return {
      ...none,
      composedOnScreen: true,
      archive: baxterArchive,
      model: baxterModel,
      screenPrompts: chatScope
        ? {
            kind: baxterSession.kind,
            messages: baxterSession.messages,
            isBusy: baxterSession.isBusy,
            unavailable: baxterSession.loadError,
            replyPath: chatPath(),
            send: (prompt) => {
              baxterSession.send(prompt).catch(() => {
                // silent-ok: send is a mutation carrying meta.errorTitle, so the global
                // MutationCache toast fires and the chat page renders session.error.
              });
            },
          }
        : null,
    };
  }
  // The bar's model picker is whichever agent the panel is showing. No archive:
  // the planner keeps one conversation per initiative, and the panel's Baxter
  // tab owns its own "New chat".
  if (plannerOnScreen) {
    return {
      ...none,
      composedOnScreen: true,
      model: initiativeAgent === "baxter" ? initiativeBaxterModel : plannerModel,
      screenPrompts: initiativeAgent === "baxter" ? initiativeBaxterPrompts : plannerPrompts,
    };
  }
  if (onBranchTriage) {
    return branch
      ? {
          session: branchSession,
          label: `Branch · ${branch}`,
          ticketId: null,
          pendingApprovals: [],
          branch,
          archive: null,
          model: null,
          composedOnScreen: false,
          screenPrompts: null,
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
      model: null,
      composedOnScreen: false,
      screenPrompts: null,
    };
  }
  if (!chatScope) return none;
  return {
    session: baxterSession,
    label: `Baxter · ${chatScopeLabel(chatScope, initiatives)}`,
    ticketId: null,
    pendingApprovals: baxterSession.pendingApprovals,
    branch: null,
    composedOnScreen: false,
    screenPrompts: null,
    archive: baxterArchive,
    model: baxterModel,
  };
}
