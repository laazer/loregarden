import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { DEFAULT_RUNTIME } from "../lib/runtimeSettings";
import type { ChatSession } from "../lib/chatSession";
import { isRunStatusBusy } from "../lib/chatSession";
import {
  fetchBranchChat,
  sendBranchChatMessage,
  type BranchTriageChatSnapshot,
} from "../lib/branchTriageApi";

/**
 * The branch triage conversation as a `ChatSession`.
 *
 * Owns the snapshot query and the send, including the optimistic append and its
 * rollback — moved here verbatim so the behaviour is unchanged and so anything
 * else binding to this conversation gets the same treatment rather than a
 * simpler send that makes the panel feel different from the dock.
 *
 * `snapshot` comes back alongside because the panel needs branch-specific
 * fields — the linked ticket, the saved runtime — that are not part of a
 * conversation and do not belong in the shared descriptor.
 */
export function useBranchChatSession(
  workspaceSlug: string,
  branch: string,
): ChatSession & { snapshot: BranchTriageChatSnapshot | undefined; isFetching: boolean } {
  const qc = useQueryClient();

  const chatQueryKey = useMemo(
    () => ["branch-triage-chat", workspaceSlug, branch] as const,
    [workspaceSlug, branch],
  );

  const chat = useQuery({
    queryKey: chatQueryKey,
    queryFn: () => fetchBranchChat(workspaceSlug, branch),
    enabled: Boolean(workspaceSlug && branch),
    refetchInterval: (query) =>
      query.state.data && query.state.data.run_status !== "idle" ? 2000 : 5000,
  });

  const sendMessage = useMutation({
    mutationFn: (content: string) => sendBranchChatMessage(workspaceSlug, branch, content),
    onMutate: async (content) => {
      await qc.cancelQueries({ queryKey: chatQueryKey });
      const previous = qc.getQueryData<BranchTriageChatSnapshot>(chatQueryKey);
      const optimisticMessage = {
        id: `pending-${Date.now()}`,
        role: "user",
        content,
        created_at: new Date().toISOString(),
      };
      qc.setQueryData<BranchTriageChatSnapshot>(chatQueryKey, (current) => {
        if (current) {
          return {
            ...current,
            messages: [...current.messages, optimisticMessage],
            run_status: "running",
          };
        }
        return {
          workspace_id: "",
          branch,
          linked_ticket_id: null,
          linked_ticket_external_id: null,
          messages: [optimisticMessage],
          runtime: DEFAULT_RUNTIME,
          run_status: "running",
          active_turn_id: null,
        };
      });
      return { previous };
    },
    onError: (_error, _content, context) => {
      if (context?.previous) {
        qc.setQueryData(chatQueryKey, context.previous);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["branch-triage", workspaceSlug] });
    },
    // The reply lands via polling, not this response — resync with the server either way.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: chatQueryKey });
    },
  });

  return {
    kind: "branch-triage",
    id: `${workspaceSlug}#${branch}`,
    messages: chat.data?.messages ?? [],
    // The mutation's pending flag covers the gap between the POST resolving and
    // the next poll reporting "running"; without it the composer flickers idle.
    isBusy: isRunStatusBusy(chat.data?.run_status) || sendMessage.isPending,
    isLoading: chat.isLoading,
    loadError: chat.isError,
    error: sendMessage.isError
      ? (sendMessage.error as Error)?.message || "Failed to send message"
      : null,
    send: (content) => sendMessage.mutateAsync(content),
    snapshot: chat.data,
    isFetching: chat.isFetching,
  };
}
