import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import { api } from "../api/client";
import type { InitiativeView, WorkspaceSummary } from "../api/client";
import { chatScopeInitiativeId, GLOBAL_CHAT_SCOPE, parseChatScope } from "../lib/chatScope";
import { useUiStore } from "../state/uiStore";

/** The workspace filter's "show everything" value, which is not a slug. */
const ALL_WORKSPACES = "all";

export interface ChatScopeState {
  /** A scope key (`lib/chatScope`). Empty only while workspaces load, or when none exist. */
  scope: string;
  setScope: (scope: string) => void;
  workspaces: WorkspaceSummary[];
  /** Initiatives a global chat can focus on: open ones, plus the one in use. */
  initiatives: InitiativeView[];
  /** False until the initiative list has loaded or failed. */
  initiativesLoaded: boolean;
  /** Whether loading the initiative list failed; the picker says so. */
  initiativesError: boolean;
}

const CLOSED_STATES = new Set(["done", "wont_do"]);

/**
 * Which archive Baxter chat is on, without the pin.
 *
 * A workspace confines the chat to that workspace. The global scopes answer
 * across every workspace, which is what an initiative needs, since its
 * milestones can live in any of them. The Console's "all" is not a chat scope:
 * it filters, and inheriting it would silently open a global chat.
 *
 * Read by surfaces that merely follow the conversation — the global action bar,
 * for one. They must not pin: pinning on app start would freeze chat to
 * whatever the Console filter happened to be then, before the user ever opened
 * a conversation.
 */
export function useChatScopeKey(): string {
  const chatScope = useUiStore((s) => s.chatScope);
  const filterWorkspace = useUiStore((s) => s.workspace);
  const { data } = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });

  return useMemo(() => {
    if (chatScope) return chatScope;
    if (filterWorkspace && filterWorkspace !== ALL_WORKSPACES) return filterWorkspace;
    return data?.[0]?.slug ?? "";
  }, [chatScope, filterWorkspace, data]);
}

/**
 * The chat scope, pinning the inherited one on first resolve.
 *
 * Shared by the page and the topbar picker on purpose — if each resolved the
 * fallback itself they could disagree, and the picker would name a scope other
 * than the one answering.
 */
export function useChatScope(): ChatScopeState {
  const chatScope = useUiStore((s) => s.chatScope);
  const setScope = useUiStore((s) => s.setChatScope);
  const scope = useChatScopeKey();

  const workspacesQ = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const initiativesQ = useQuery({ queryKey: ["initiatives"], queryFn: api.initiatives });
  const focusedId = chatScopeInitiativeId(scope);

  const initiatives = useMemo(
    () =>
      (initiativesQ.data ?? []).filter(
        (initiative) => initiative.id === focusedId || !CLOSED_STATES.has(initiative.state),
      ),
    [initiativesQ.data, focusedId],
  );

  // Pin the inherited scope so a later Console change cannot silently move an
  // open conversation to another workspace.
  useEffect(() => {
    if (!chatScope && scope) setScope(scope);
  }, [chatScope, scope, setScope]);

  // A focus on an initiative that has since been deleted would 404 on every
  // send. Widen to the unfocused global archive, which is what the operator
  // was nearest to, rather than leave the composer pointed at nothing.
  useEffect(() => {
    if (focusedId && initiativesQ.isSuccess && !initiativesQ.data.some((i) => i.id === focusedId)) {
      setScope(GLOBAL_CHAT_SCOPE);
    }
  }, [focusedId, initiativesQ.isSuccess, initiativesQ.data, setScope]);

  return {
    scope,
    setScope,
    workspaces: workspacesQ.data ?? [],
    initiatives,
    initiativesLoaded: !initiativesQ.isPending,
    initiativesError: initiativesQ.isError,
  };
}

/** A short name for a scope, for labels such as "Baxter · …". */
export function chatScopeLabel(scope: string, initiatives: readonly InitiativeView[] = []): string {
  const parsed = parseChatScope(scope);
  if (!parsed) return "";
  if (parsed.kind === "workspace") return parsed.slug;
  if (!parsed.initiativeId) return "All workspaces";
  const initiative = initiatives.find((i) => i.id === parsed.initiativeId);
  return initiative ? initiative.external_id : "Initiative";
}
