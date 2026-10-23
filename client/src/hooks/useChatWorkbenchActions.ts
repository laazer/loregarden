import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { baxterChatWorkbenchApi } from "../api/baxterChatApi";
import type { ChatFocus, FeatureLoopStartRequest } from "../api/chatWorkbenchTypes";
import { api, type BaxterChatSnapshot, type InitiativeView } from "../api/client";
import type { ChatWorkbenchState } from "../components/chat/ChatSideCard";
import { isGlobalChatScope, parseChatScope } from "../lib/chatScope";
import {
  baxterChatSessionKey,
  baxterChatSessionsKey,
  type BaxterChatSessionBinding,
} from "./useBaxterChatSession";

/**
 * The workbench cards' writes: focus, starting a feature loop, and deciding
 * its criteria. Each lands the server's fresh snapshot in the thread's cache,
 * so the cards and the thread read one answer.
 *
 * Focus and starting a loop work before the first message: they create the
 * thread, as saving the runtime does, because both are things an operator
 * sets up *before* asking.
 */
export function useChatWorkbenchActions({
  scope,
  sessionId,
  openSession,
}: {
  scope: string;
  sessionId: string;
  openSession: (id: string) => void;
}) {
  const qc = useQueryClient();

  const ensureSession = async (): Promise<string> => {
    if (sessionId) return sessionId;
    const created = await api.createBaxterChatSession(scope);
    openSession(created.id);
    qc.setQueryData(baxterChatSessionKey(scope, created.id), created);
    return created.id;
  };

  const land = (snapshot: BaxterChatSnapshot) => {
    qc.setQueryData(baxterChatSessionKey(scope, snapshot.id), snapshot);
    void qc.invalidateQueries({ queryKey: baxterChatSessionsKey(scope) });
  };

  const setFocus = useMutation({
    meta: { errorTitle: "Change chat focus" },
    mutationFn: async (body: { workspace_slugs: string[]; initiative_ids: string[] }) =>
      baxterChatWorkbenchApi.setFocus(scope, await ensureSession(), body),
    onSuccess: land,
  });

  const startLoop = useMutation({
    meta: { errorTitle: "Start feature loop" },
    mutationFn: async (body: FeatureLoopStartRequest) =>
      baxterChatWorkbenchApi.startFeatureLoop(scope, await ensureSession(), body),
    onSuccess: land,
  });

  const addCriterion = useMutation({
    meta: { errorTitle: "Add criterion" },
    mutationFn: (text: string) => baxterChatWorkbenchApi.addCriterion(scope, sessionId, text),
    onSuccess: land,
  });

  const decide = useMutation({
    meta: { errorTitle: "Decide criteria" },
    mutationFn: ({ ids, accept }: { ids: string[]; accept: boolean }) =>
      baxterChatWorkbenchApi.decideCriteria(scope, sessionId, ids, accept),
    onSuccess: land,
  });

  return { setFocus, startLoop, addCriterion, decide };
}

export type ChatWorkbenchActions = ReturnType<typeof useChatWorkbenchActions>;

/**
 * Before the first message there is no snapshot, so the Scope card reads the
 * focus the archive implies: its workspace, or every workspace and its
 * initiative.
 */
function impliedFocus(scope: string, initiatives: readonly InitiativeView[]): ChatFocus {
  const parsed = parseChatScope(scope);
  if (parsed?.kind === "workspace") {
    return {
      all_workspaces: false,
      workspaces: [{ id: parsed.slug, slug: parsed.slug, fixed: true }],
      initiatives: [],
    };
  }
  const initiative = parsed?.initiativeId
    ? initiatives.find((item) => item.id === parsed.initiativeId)
    : undefined;
  return {
    all_workspaces: true,
    workspaces: [],
    initiatives: initiative
      ? [{ id: initiative.id, external_id: initiative.external_id, title: initiative.title, fixed: true }]
      : [],
  };
}

/** Everything the workbench's side cards read and do, for one thread. */
export function useChatWorkbenchState({
  scope,
  chat,
  initiatives,
}: {
  scope: string;
  chat: BaxterChatSessionBinding;
  initiatives: readonly InitiativeView[];
}): ChatWorkbenchState {
  const actions = useChatWorkbenchActions({
    scope,
    sessionId: chat.sessionId,
    openSession: chat.openSession,
  });
  const workspacesQ = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const snapshot = chat.snapshot;
  const focus = snapshot?.focus ?? impliedFocus(scope, initiatives);
  const hostSlug = snapshot?.host_workspace_slug ?? null;
  const live = (workspacesQ.data ?? []).filter((w) => !w.archived_at).map((w) => w.slug);
  const loopWorkspaces = focus.all_workspaces ? live : focus.workspaces.map((w) => w.slug);
  return {
    focus,
    isGlobal: isGlobalChatScope(scope),
    hostSlug,
    feature: snapshot?.feature ?? null,
    chatBranch: snapshot?.chat_branch ?? null,
    loopWorkspaces,
    defaultLoopWorkspace:
      hostSlug && loopWorkspaces.includes(hostSlug) ? hostSlug : (loopWorkspaces[0] ?? ""),
    savingFocus: actions.setFocus.isPending,
    startingLoop: actions.startLoop.isPending,
    decidingCriteria: actions.decide.isPending || actions.addCriterion.isPending,
    onSaveFocus: (body) => actions.setFocus.mutateAsync(body),
    onStartLoop: (body) => actions.startLoop.mutateAsync(body),
    onDecideCriteria: (ids, accept) =>
      actions.decide.mutate({ ids, accept }),
    onAddCriterion: (text) => actions.addCriterion.mutateAsync(text),
  };
}
