import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api, type Approval } from "../api/client";
import type { TicketSummary, WorkspaceRuntimeSettings } from "../api/client";
import { initiativeIdFromPath, ticketUuidForRef } from "../lib/appNavigation";
import { chatScopeInitiativeId, chatScopeWorkspaceSlug, initiativeChatScope } from "../lib/chatScope";
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
  /**
   * What the bound conversation is looking at, as the dock's context line:
   * "lg-external-harness-1413 · test-design", "loregarden · ux/b1", a workspace.
   * Who answers is always Baxter, so the line names the subject, not the agent.
   */
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
  /**
   * The agents that can answer on this screen and which one is bound, or null
   * where only one can. The initiative plan page offers the planner and Baxter.
   */
  agents: ChatAgents | null;
  /** Who answers in the bound conversation, as its thread names them. */
  assistantLabel: string;
  /**
   * Where this conversation opens full-page, or null when it has no page of its
   * own. Only the workspace Baxter thread does: `/chat` shows the same thread,
   * so maximising the dock is a navigation, not a copy.
   */
  maximisePath: string | null;
  /** Which openers apply: a `TRY_ASKING` key in `lib/dockChatPrompts`; "" for none. */
  promptKind: string;
  /**
   * Why the composer cannot send to the bound conversation right now, or null.
   * Distinct from a load failure: the thread is fine, there is just nothing it
   * can do yet (the planner, before an initiative has a milestone).
   */
  composerBlocked: string | null;
}

/** An agent the bar can switch the conversation to. */
export interface ChatAgentOption {
  id: string;
  label: string;
  /** What it answers, for the switch's tooltip. */
  description: string;
}

export interface ChatAgents {
  options: readonly ChatAgentOption[];
  /** The bound agent's `id`. */
  active: string;
  select: (id: string) => void;
}

// Baxter's two modes on an initiative. Behind them sit two agents with their
// own threads, but the operator talks to one assistant: the mode says what it
// is doing, not who it is.
const INITIATIVE_AGENTS: readonly ChatAgentOption[] = [
  {
    id: "planner",
    label: "Plan",
    description: "Baxter proposes dates you accept, marks work for a person, and can start work or run the autopilot",
  },
  {
    id: "baxter",
    label: "Triage",
    description: "Baxter triages the work under this initiative across its workspaces: what is blocked, what to file, what to change",
  },
];

/**
 * What the bar needs to offer one-click prompts into a conversation the page
 * composes for itself: Home and the chat page, for the Baxter thread.
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
  // An initiative's plan page binds its own agents — the planner, or Baxter
  // focused on the initiative — not the workspace Baxter thread.
  const initiativeId = initiativeIdFromPath(pathname);
  const plannerOnScreen = initiativeId !== null;
  const initiativeAgent = useInitiativeAgentStore((s) => s.agent);
  const chatScope = useChatScopeKey();
  // Only a focused chat's label needs the list; the key is the picker's, so it is cached.
  const focusedInitiative = chatScopeInitiativeId(chatScope);
  const { data: initiatives } = useQuery({
    queryKey: ["initiatives"],
    queryFn: api.initiatives,
    enabled: focusedInitiative !== null || plannerOnScreen,
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
  // The path carries the shareable ref; the plan page keys everything by the
  // UUID, so resolve it the way the route does (null until the route learns
  // it). Asking by the ref 404s.
  const initiativeUuid = useTicketRefStore((s) =>
    initiativeId ? ticketUuidForRef(initiativeId, s.uuidByRef) : null,
  );
  const setInitiativeAgent = useInitiativeAgentStore((s) => s.setAgent);
  // Same query key as the plan page's Draft button, so binding it here costs no
  // request of its own; an empty id issues none off the plan page.
  const planner = useInitiativePlanner(initiativeUuid ?? "");
  const plannerModel: ChatModelBinding = {
    assistant: "Baxter, planning",
    scopeLabel: "Baxter · plan",
    workspaceSlug: planner.workspaceSlug,
    runtime: planner.runtime,
    setRuntime: planner.setRuntime,
    isSavingRuntime: planner.isSavingRuntime,
    isBusy: planner.isBusy,
  };
  // Same query as the plan page's: the planner has nothing to schedule until a
  // milestone is attached, so the composer says so instead of sending.
  const plan = useQuery({
    queryKey: planQueryKey(initiativeUuid ?? ""),
    queryFn: () => api.initiativePlan(initiativeUuid as string),
    enabled: Boolean(initiativeUuid) && initiativeAgent === "planner",
  });
  // Bound only while it is the agent shown; the planner's thread is per
  // initiative and needs no binding to keep.
  const initiativeBaxter = useInitiativeBaxterSession(
    initiativeAgent === "baxter" ? initiativeUuid ?? "" : "",
  );
  const initiativeBaxterModel: ChatModelBinding = {
    assistant: "Baxter",
    scopeLabel: "Baxter · initiative triage",
    // A global thread runs on its host's runtime, known once the thread exists.
    workspaceSlug: initiativeBaxter.snapshot?.host_workspace_slug || "",
    runtime: initiativeBaxter.runtime,
    setRuntime: initiativeBaxter.setRuntime,
    isSavingRuntime: initiativeBaxter.isSavingRuntime,
    isBusy: initiativeBaxter.isBusy,
  };
  const initiativeBaxterArchive: ChatArchive | null = initiativeUuid
    ? {
        scope: initiativeChatScope(initiativeUuid),
        sessionId: initiativeBaxter.sessionId,
        openSession: initiativeBaxter.openSession,
        startNewChat: initiativeBaxter.startNewChat,
        sendInNewChat: initiativeBaxter.sendInNewChat,
        forkSession: initiativeBaxter.forkSession,
        forkFromMessage: initiativeBaxter.forkFromMessage,
      }
    : null;
  const initiativeAgents: ChatAgents = {
    options: INITIATIVE_AGENTS,
    active: initiativeAgent,
    select: (id) => setInitiativeAgent(id === "baxter" ? "baxter" : "planner"),
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

  const none: ActiveChatSession = {
    session: null,
    label: "",
    ticketId: null,
    pendingApprovals: [],
    branch: null,
    archive: null,
    model: null,
    composedOnScreen: false,
    screenPrompts: null,
    agents: null,
    assistantLabel: "Baxter",
    maximisePath: null,
    promptKind: "",
    composerBlocked: null,
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
  // Two agents work on an initiative, and the bar binds whichever one the
  // operator picked. Each keeps its own conversation, so switching never loses
  // a thread. Only Baxter's has an archive: the planner keeps one per initiative.
  if (plannerOnScreen) {
    const initiativeRef =
      initiatives?.find((i) => i.id === initiativeUuid)?.external_id ?? initiativeId;
    if (initiativeAgent === "baxter") {
      return {
        ...none,
        session: initiativeUuid ? initiativeBaxter : null,
        label: initiativeRef,
        pendingApprovals: initiativeBaxter.pendingApprovals,
        archive: initiativeBaxterArchive,
        model: initiativeBaxterModel,
        agents: initiativeAgents,
        promptKind: INITIATIVE_TRIAGE,
      };
    }
    return {
      ...none,
      session: initiativeUuid ? planner.session : null,
      label: initiativeRef,
      model: plannerModel,
      agents: initiativeAgents,
      promptKind: INITIATIVE_PLANNING,
      composerBlocked:
        plan.data && plan.data.milestones.length === 0
          ? "Attach a milestone to this initiative, then Baxter can schedule it"
          : null,
    };
  }
  if (onBranchTriage) {
    return branch
      ? {
          ...none,
          session: branchSession,
          label: branchWorkspace ? `${branchWorkspace} · ${branch}` : branch,
          branch,
          promptKind: branchSession.kind,
        }
      : none;
  }
  if (ticketId) {
    return {
      ...none,
      session: ticketSession,
      label: ticketContext(ticket),
      ticketId,
      pendingApprovals: pending,
      branch: ticket?.branch || null,
      promptKind: ticketSession.kind,
    };
  }
  if (!chatScope) return none;
  return {
    ...none,
    session: baxterSession,
    label: chatScopeLabel(chatScope, initiatives),
    pendingApprovals: baxterSession.pendingApprovals,
    archive: baxterArchive,
    model: baxterModel,
    maximisePath: chatPath(),
    promptKind: baxterSession.kind,
  };
}

/**
 * A ticket conversation's context line: its id and the stage it is in, the two
 * things that say which run Baxter is reading. The id alone until it loads.
 */
export function ticketContext(
  ticket: Pick<TicketSummary, "external_id" | "workflow_stage_key"> | undefined,
): string {
  if (!ticket) return "";
  return ticket.workflow_stage_key
    ? `${ticket.external_id} · ${ticket.workflow_stage_key}`
    : ticket.external_id;
}
