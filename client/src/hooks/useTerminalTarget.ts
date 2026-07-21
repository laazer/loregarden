import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api } from "../api/client";
import { ticketIdFromPath } from "../lib/appNavigation";
import { useUiStore } from "../state/uiStore";

export interface TerminalTarget {
  /** Empty when the current screen names no single workspace. */
  workspaceSlug: string;
  agent: string;
}

/** The workspace filter's "show everything" value, which is not a slug. */
const ALL_WORKSPACES = "all";

/**
 * Which workspace a shell opened from the dock should start in.
 *
 * Derived from the route for the same reason the chat binding is: a remembered
 * target would survive navigation and open a shell in a repo the screen is no
 * longer showing — which, for a terminal, means running commands somewhere the
 * operator is not looking.
 *
 * No branch is returned. The shell opens in the workspace root, so a ticket's
 * branch would be a label that is true only by coincidence — and was observed
 * disagreeing with the shell's own prompt. The agent is orientation only; the
 * shell is scoped to the workspace and outlives any run, which is exactly when
 * someone wants to poke around.
 */
export function useTerminalTarget(): TerminalTarget {
  const { pathname } = useLocation();
  const branchWorkspace = useUiStore((s) => s.branchTriageWorkspaceSlug);
  const filterWorkspace = useUiStore((s) => s.workspace);

  const onBranchTriage = pathname.startsWith("/branch-triage");
  const ticketId = onBranchTriage ? null : ticketIdFromPath(pathname);

  // Same key the dashboard uses, so being on a ticket costs no extra request.
  const { data: ticket } = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => api.ticket(ticketId as string),
    enabled: Boolean(ticketId),
  });

  if (onBranchTriage) {
    return { workspaceSlug: branchWorkspace, agent: "" };
  }

  if (ticket) {
    return { workspaceSlug: ticket.workspace_slug, agent: ticket.next_agent };
  }

  // Nothing on screen names a workspace, so fall back to the filter — unless
  // it is showing all of them, which names none.
  return {
    workspaceSlug: filterWorkspace === ALL_WORKSPACES ? "" : filterWorkspace,
    agent: "",
  };
}
