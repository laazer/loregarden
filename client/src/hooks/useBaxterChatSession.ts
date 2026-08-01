import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo } from "react";

import { ApiError, api, type BaxterChatSnapshot } from "../api/client";
import type { ChatSession } from "../lib/chatSession";
import { isRunStatusBusy } from "../lib/chatSession";
import { useUiStore } from "../state/uiStore";

export interface BaxterChatSessionBinding extends ChatSession {
  /** The open thread's server id, or "" before the first message creates one. */
  sessionId: string;
  title: string;
  snapshot: BaxterChatSnapshot | undefined;
  openSession: (id: string) => void;
  startNewChat: () => void;
  renameSession: (id: string, title: string) => Promise<unknown>;
  deleteSession: (id: string) => Promise<unknown>;
}

export function baxterChatSessionsKey(slug: string) {
  return ["baxter-chat-sessions", slug] as const;
}

function baxterChatSessionKey(slug: string, sessionId: string) {
  return ["baxter-chat-session", slug, sessionId] as const;
}

/**
 * The Home Baxter conversation as a `ChatSession`.
 *
 * The thread is server-owned: messages, the busy flag, and the reply all come
 * from the snapshot rather than from the send promise, so a reload or a dropped
 * connection costs the response and not the answer.
 *
 * A thread is created lazily on the first send. Opening Home would otherwise
 * mint an empty row in the archive every time, and an archive full of blank
 * conversations is worse than none.
 */
export function useBaxterChatSession(workspaceSlug: string): BaxterChatSessionBinding {
  const qc = useQueryClient();
  const sessionId = useUiStore((s) => s.baxterChatSessionId);
  const setSessionId = useUiStore((s) => s.setBaxterChatSessionId);

  const enabled = Boolean(workspaceSlug && sessionId);
  const queryKey = useMemo(
    () => baxterChatSessionKey(workspaceSlug, sessionId),
    [workspaceSlug, sessionId],
  );

  const snapshot = useQuery({
    queryKey,
    queryFn: () => api.baxterChatSession(workspaceSlug, sessionId),
    enabled,
    // Poll fast only while a turn is in flight; the reply arrives this way.
    refetchInterval: (query) =>
      query.state.data && query.state.data.run_status !== "idle" ? 1500 : 10_000,
    retry: false,
  });

  // A thread deleted elsewhere (or a stale id from a previous database) must not
  // leave Home stuck on a conversation the server does not have.
  useEffect(() => {
    if (snapshot.error instanceof ApiError && snapshot.error.status === 404) {
      setSessionId("");
    }
  }, [snapshot.error, setSessionId]);

  const invalidateArchive = useCallback(() => {
    qc.invalidateQueries({ queryKey: baxterChatSessionsKey(workspaceSlug) });
  }, [qc, workspaceSlug]);

  const sendMessage = useMutation({
    meta: { errorTitle: "Send message" },
    mutationFn: async (content: string) => {
      let id = sessionId;
      if (!id) {
        const created = await api.createBaxterChatSession(workspaceSlug);
        id = created.id;
        setSessionId(id);
      }
      return api.sendBaxterChatMessage(workspaceSlug, id, content);
    },
    onSuccess: (result) => {
      // The POST returns the thread with the user turn already on it, so the
      // message appears without waiting a poll interval.
      qc.setQueryData(baxterChatSessionKey(workspaceSlug, result.id), result);
      invalidateArchive();
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["baxter-chat-session", workspaceSlug] });
    },
  });

  const renameSession = useMutation({
    meta: { errorTitle: "Rename session" },
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.renameBaxterChatSession(workspaceSlug, id, title),
    onSuccess: (result) => {
      qc.setQueryData(baxterChatSessionKey(workspaceSlug, result.id), result);
      invalidateArchive();
    },
  });

  const deleteSession = useMutation({
    meta: { errorTitle: "Delete session" },
    mutationFn: (id: string) => api.deleteBaxterChatSession(workspaceSlug, id),
    onSuccess: (_result, id) => {
      if (id === sessionId) setSessionId("");
      invalidateArchive();
    },
  });

  return {
    kind: "baxter-home",
    id: sessionId,
    sessionId,
    title: snapshot.data?.title ?? "",
    messages: snapshot.data?.messages ?? [],
    // The in-flight POST covers the gap before the first poll reports "running".
    isBusy: isRunStatusBusy(snapshot.data?.run_status) || sendMessage.isPending,
    isLoading: enabled && snapshot.isLoading,
    // A missing thread is recovered from above, so it is not a load failure.
    loadError:
      snapshot.isError && !(snapshot.error instanceof ApiError && snapshot.error.status === 404),
    error: sendMessage.isError
      ? (sendMessage.error as Error)?.message || "Failed to send message"
      : null,
    send: (content: string) => sendMessage.mutateAsync(content),
    snapshot: snapshot.data,
    openSession: setSessionId,
    startNewChat: () => setSessionId(""),
    renameSession: (id: string, title: string) => renameSession.mutateAsync({ id, title }),
    deleteSession: (id: string) => deleteSession.mutateAsync(id),
  };
}
