import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { pathForPage, initiativePath, ticketPath, viewPath } from "../lib/appNavigation";
import { fetchBranchTriage } from "../lib/branchTriageApi";
import { describeError } from "../state/toastStore";
import { ticketStateChip } from "../lib/ticketStates";
import type { ChipStatus } from "../lib/statusTone";
import {
  matchPages,
  matchText,
  searchNotes,
  type SearchGroup,
  type SearchSourceState,
} from "../lib/topbarSearch";
import { fetchViews, viewsKeys } from "../lib/viewsApi";
import { useSidebarWorkspaceSlug } from "../state/SidebarWorkspaceContext";
import { useUiStore } from "../state/uiStore";
import { baxterChatSessionsKey } from "./useBaxterChatSession";
import { useChatScopeKey } from "./useChatScope";

/** One thing search can open. `go` takes the operator there. */
export interface SearchResult {
  key: string;
  group: SearchGroup;
  title: string;
  /** A monospace id drawn before the title: a ticket's or an initiative's. */
  code?: string;
  /** A few words after the title. */
  meta?: string;
  chip?: ChipStatus;
  icon?: ReactNode;
  go: () => void;
}

/** Below this, a server search (tickets) or a git scan (branches) would match too much to be worth asking. */
export const SERVER_SEARCH_MIN_CHARS = 2;
const PER_GROUP = 5;
const TICKET_LIMIT = 8;
/** Long enough to skip the keystrokes of a word being typed, short enough to feel live. */
const DEBOUNCE_MS = 200;

function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return settled;
}

function errorText(error: unknown): string {
  return describeError(error, "request failed");
}

/**
 * Everything the topbar search can open for `query`, grouped, plus which
 * sources are still answering or failed.
 *
 * Pages and the lists the app already holds are filtered here: tabs (the
 * sidebar's own views), initiatives, and the chat threads in the current chat
 * scope. Tickets are a server search. Branches are the Branch Triage scan of
 * the sidebar's workspace, a git walk, so it waits for two characters, and is
 * cached for a minute under the key that page uses. Every list reuses its
 * page's query key, so search costs nothing a page already paid for, and the
 * two never disagree. Nothing is fetched until the box has text in it.
 */
export function useTopbarSearchResults(query: string, open: boolean) {
  const navigate = useNavigate();
  const text = query.trim();
  const settled = useDebounced(text, DEBOUNCE_MS);
  const slug = useSidebarWorkspaceSlug();
  const chatScope = useChatScopeKey();
  const openChat = useUiStore((s) => s.setBaxterChatSessionId);
  const setBranchWorkspace = useUiStore((s) => s.setBranchTriageWorkspaceSlug);
  const setBranch = useUiStore((s) => s.setBranchTriageBranch);

  const active = open && text !== "";
  const serverReady = active && settled.length >= SERVER_SEARCH_MIN_CHARS;
  const typing = text !== settled && text.length >= SERVER_SEARCH_MIN_CHARS;

  const tickets = useQuery({
    queryKey: ["topbar-search", "tickets", settled],
    queryFn: () => api.tickets({ search: settled, limit: TICKET_LIMIT }),
    enabled: serverReady,
    staleTime: 15_000,
    // Search's own key: its failure is drawn in the results, where the
    // operator is looking. The shared keys below keep their pages' toasts.
    meta: { suppressErrorToast: true },
  });
  const views = useQuery({
    queryKey: viewsKeys.views(slug),
    queryFn: () => fetchViews(slug),
    enabled: active && slug !== "",
  });
  const initiatives = useQuery({
    queryKey: ["initiatives"],
    queryFn: api.initiatives,
    enabled: active,
    staleTime: 60_000,
  });
  const chats = useQuery({
    queryKey: baxterChatSessionsKey(chatScope),
    queryFn: () => api.baxterChatSessions(chatScope),
    enabled: active && chatScope !== "",
    staleTime: 30_000,
  });
  const branches = useQuery({
    queryKey: ["branch-triage", slug],
    queryFn: () => fetchBranchTriage(slug),
    enabled: serverReady && slug !== "",
    staleTime: 60_000,
  });

  const results = useMemo((): SearchResult[] => {
    if (!text) return [];
    const pages = matchPages(text).map((page): SearchResult => ({
      key: `page-${page.key}`,
      group: "Pages",
      title: page.label,
      meta: page.meta,
      icon: page.icon,
      go: () => navigate(page.path),
    }));
    const tabs = matchText(views.data ?? [], text, (view) => [view.title], PER_GROUP).map(
      (view): SearchResult => ({
        key: `tab-${view.id}`,
        group: "Tabs",
        title: view.title,
        meta: "Tab",
        go: () => navigate(viewPath(view.id)),
      }),
    );
    const ticketRows =
      settled.length >= SERVER_SEARCH_MIN_CHARS
        ? (tickets.data ?? []).map((ticket): SearchResult => ({
            key: `ticket-${ticket.id}`,
            group: "Tickets",
            title: ticket.title,
            code: ticket.external_id,
            chip: ticketStateChip(ticket.state),
            go: () => navigate(ticketPath(ticket.id)),
          }))
        : [];
    const initiativeRows = matchText(
      initiatives.data ?? [],
      text,
      (initiative) => [initiative.title, initiative.external_id],
      PER_GROUP,
    ).map((initiative): SearchResult => ({
      key: `initiative-${initiative.id}`,
      group: "Initiatives",
      title: initiative.title,
      code: initiative.external_id,
      chip: ticketStateChip(initiative.state),
      go: () => navigate(initiativePath(initiative.id)),
    }));
    const chatRows = matchText(
      chats.data ?? [],
      text,
      (chat) => [chat.title, chat.preview],
      PER_GROUP,
    ).map((chat): SearchResult => ({
      key: `chat-${chat.id}`,
      group: "Chats",
      title: chat.title || chat.preview || "Untitled chat",
      meta: `${chat.message_count} message${chat.message_count === 1 ? "" : "s"}`,
      go: () => {
        openChat(chat.id);
        navigate(pathForPage("chat"));
      },
    }));
    const branchRows =
      settled.length >= SERVER_SEARCH_MIN_CHARS
        ? matchText(branches.data?.branches ?? [], text, (branch) => [branch.name], PER_GROUP).map(
            (branch): SearchResult => ({
              key: `branch-${branch.name}`,
              group: "Branches",
              title: branch.name,
              meta: branch.is_current ? "Checked out" : "Branch",
              go: () => {
                setBranchWorkspace(slug);
                setBranch(branch.name);
                navigate(pathForPage("branch-triage"));
              },
            }),
          )
        : [];
    return [...pages, ...tabs, ...ticketRows, ...initiativeRows, ...chatRows, ...branchRows];
  }, [
    text,
    settled,
    navigate,
    views.data,
    tickets.data,
    initiatives.data,
    chats.data,
    branches.data,
    openChat,
    setBranchWorkspace,
    setBranch,
    slug,
  ]);

  const sources: SearchSourceState[] = [
    {
      group: "Tabs",
      pending: views.isLoading,
      error: views.isError ? errorText(views.error) : null,
    },
    {
      group: "Tickets",
      pending: typing || tickets.isFetching,
      error: tickets.isError ? errorText(tickets.error) : null,
    },
    {
      group: "Initiatives",
      pending: initiatives.isLoading,
      error: initiatives.isError ? errorText(initiatives.error) : null,
    },
    {
      group: "Chats",
      pending: chats.isLoading,
      error: chats.isError ? errorText(chats.error) : null,
    },
    {
      group: "Branches",
      pending: (typing && slug !== "") || branches.isLoading,
      error: branches.isError ? errorText(branches.error) : null,
    },
  ];
  const notes = searchNotes(sources);

  return {
    results,
    pending: notes.pending,
    errors: notes.errors,
    /** The query is long enough for every source to have been asked. */
    complete: settled.length >= SERVER_SEARCH_MIN_CHARS && !typing,
  };
}
