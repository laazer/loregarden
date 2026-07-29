import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import { api } from "../api/client";
import type { WorkspaceSummary } from "../api/client";
import { useUiStore } from "../state/uiStore";

/** The workspace filter's "show everything" value, which is not a slug. */
const ALL_WORKSPACES = "all";

export interface ChatWorkspace {
  /** Empty only while workspaces load, or when none exist. */
  slug: string;
  setSlug: (slug: string) => void;
  workspaces: WorkspaceSummary[];
}

/**
 * The single workspace Baxter chat is confined to.
 *
 * Chat cannot run against the Console's "all": a turn is answered by one
 * workspace's agent, and the ticket refs in its reply only resolve there. The
 * slug lives in its own store field rather than reusing `workspace` so picking
 * a workspace for chat does not re-filter the Console and Home.
 *
 * Shared by the page and the topbar picker on purpose — if each resolved the
 * fallback itself they could disagree, and the picker would name a workspace
 * other than the one answering.
 */
export function useChatWorkspace(): ChatWorkspace {
  const chatWorkspaceSlug = useUiStore((s) => s.chatWorkspaceSlug);
  const setSlug = useUiStore((s) => s.setChatWorkspaceSlug);
  const filterWorkspace = useUiStore((s) => s.workspace);

  const { data } = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const workspaces = data ?? [];

  const slug = useMemo(() => {
    if (chatWorkspaceSlug) return chatWorkspaceSlug;
    if (filterWorkspace && filterWorkspace !== ALL_WORKSPACES) return filterWorkspace;
    return workspaces[0]?.slug ?? "";
  }, [chatWorkspaceSlug, filterWorkspace, workspaces]);

  // Pin the inherited slug so a later Console change cannot silently move an
  // open conversation to another workspace.
  useEffect(() => {
    if (!chatWorkspaceSlug && slug) setSlug(slug);
  }, [chatWorkspaceSlug, slug, setSlug]);

  return { slug, setSlug, workspaces };
}
