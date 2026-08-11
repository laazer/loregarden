import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo } from "react";

import { ApiError, api, type Approval, type BaxterChatSnapshot, type WorkspaceRuntimeSettings } from "../api/client";
import { baxterChatApi } from "../api/baxterChatApi";
import type { ChatSession } from "../lib/chatSession";
import { isRunStatusBusy } from "../lib/chatSession";
import { DEFAULT_RUNTIME } from "../lib/runtimeSettings";
import { useUiStore } from "../state/uiStore";

export interface BaxterChatSessionBinding extends ChatSession {
  /** The open thread's server id, or "" before the first message creates one. */
  sessionId: string;
  title: string;
  snapshot: BaxterChatSnapshot | undefined;
  /** In-flight Home-chat approvals for this thread (also listed on the board). */
  pendingApprovals: Approval[];
  openSession: (id: string) => void;
  startNewChat: () => void;
  /** Create a fresh thread and send into it, in that order. */
  sendInNewChat: (content: string) => Promise<unknown>;
  /**
   * Branch the open thread: copy settled history into a new session, switch to
   * it, and optionally send `body` as the first new turn.
   */
  forkSession: (body?: string) => Promise<unknown>;
  renameSession: (id: string, title: string) => Promise<unknown>;
  deleteSession: (id: string) => Promise<unknown>;
  /** Stop the in-flight turn and unlock the composer. */
  stop: () => Promise<unknown>;
  isStopping: boolean;
  runtime: WorkspaceRuntimeSettings;
  setRuntime: (runtime: WorkspaceRuntimeSettings) => Promise<void>;
  isSavingRuntime: boolean;
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
    // Poll fast while a turn is in flight or waiting on the operator.
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 10_000;
      if (data.run_status !== "idle") return 1500;
      if ((data.pending_approvals?.length ?? 0) > 0) return 2000;
      return 10_000;
    },
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
    mutationFn: async ({ content, skill = "" }: { content: string; skill?: string }) => {
      let id = sessionId;
      if (!id) {
        const created = await api.createBaxterChatSession(workspaceSlug);
        id = created.id;
        setSessionId(id);
      }
      return api.sendBaxterChatMessage(workspaceSlug, id, content, skill);
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

  // A note's "Send in new chat" cannot be `startNewChat()` followed by `send()`:
  // the id this hook closes over is still the old thread's for the rest of the
  // tick, so the message would land in the conversation the operator just left.
  // Creating the thread first makes the target explicit.
  const sendInNewChat = useMutation({
    meta: { errorTitle: "Send message" },
    mutationFn: async (content: string) => {
      const created = await api.createBaxterChatSession(workspaceSlug);
      setSessionId(created.id);
      return api.sendBaxterChatMessage(workspaceSlug, created.id, content);
    },
    onSuccess: (result) => {
      qc.setQueryData(baxterChatSessionKey(workspaceSlug, result.id), result);
      invalidateArchive();
    },
  });

  const forkSession = useMutation({
    meta: { errorTitle: "Fork chat" },
    mutationFn: async (body: string = "") => {
      if (!sessionId) throw new Error("No chat session to fork");
      const forked = await baxterChatApi.forkSession(workspaceSlug, sessionId);
      setSessionId(forked.id);
      qc.setQueryData(baxterChatSessionKey(workspaceSlug, forked.id), forked);
      invalidateArchive();
      const text = body.trim();
      if (!text) return forked;
      return api.sendBaxterChatMessage(workspaceSlug, forked.id, text);
    },
    onSuccess: (result) => {
      qc.setQueryData(baxterChatSessionKey(workspaceSlug, result.id), result);
      invalidateArchive();
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

  const stopTurn = useMutation({
    meta: { errorTitle: "Stop turn" },
    mutationFn: async () => {
      if (!sessionId) throw new Error("No chat session");
      return api.stopBaxterChatTurn(workspaceSlug, sessionId);
    },
    onSuccess: (result) => {
      qc.setQueryData(baxterChatSessionKey(workspaceSlug, result.id), result);
      invalidateArchive();
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["baxter-chat-session", workspaceSlug] });
    },
  });

  const saveRuntime = useMutation({
    meta: { errorTitle: "Save chat runtime" },
    mutationFn: async (runtime: WorkspaceRuntimeSettings) => {
      let id = sessionId;
      if (!id) {
        const created = await api.createBaxterChatSession(workspaceSlug);
        id = created.id;
        setSessionId(id);
        qc.setQueryData(baxterChatSessionKey(workspaceSlug, id), created);
        invalidateArchive();
      }
      return api.setBaxterChatRuntime(workspaceSlug, id, runtime);
    },
    onSuccess: (runtime, _variables) => {
      const id = sessionId || useUiStore.getState().baxterChatSessionId;
      if (id) {
        qc.setQueryData<BaxterChatSnapshot>(
          baxterChatSessionKey(workspaceSlug, id),
          (current) => (current ? { ...current, runtime } : current),
        );
      }
      invalidateArchive();
    },
  });

  return {
    kind: "baxter-home",
    id: sessionId,
    sessionId,
    title: snapshot.data?.title ?? "",
    messages: snapshot.data?.messages ?? [],
    pendingApprovals: snapshot.data?.pending_approvals ?? [],
    // The in-flight POST covers the gap before the first poll reports "running".
    isBusy: isRunStatusBusy(snapshot.data?.run_status) || sendMessage.isPending,
    activeTurnId: snapshot.data?.active_turn_id ?? null,
    isLoading: enabled && snapshot.isLoading,
    // A missing thread is recovered from above, so it is not a load failure.
    loadError:
      snapshot.isError && !(snapshot.error instanceof ApiError && snapshot.error.status === 404),
    error: sendMessage.isError
      ? (sendMessage.error as Error)?.message || "Failed to send message"
      : saveRuntime.isError
        ? (saveRuntime.error as Error)?.message || "Failed to save model settings"
      : null,
    send: (content: string, options) =>
      sendMessage.mutateAsync({ content, skill: options?.skill }),
    sendInNewChat: (content: string) => sendInNewChat.mutateAsync(content),
    forkSession: (body = "") => forkSession.mutateAsync(body),
    stop: () => stopTurn.mutateAsync(),
    isStopping: stopTurn.isPending,
    snapshot: snapshot.data,
    openSession: setSessionId,
    startNewChat: () => setSessionId(""),
    renameSession: (id: string, title: string) => renameSession.mutateAsync({ id, title }),
    deleteSession: (id: string) => deleteSession.mutateAsync(id),
    runtime: snapshot.data?.runtime ?? DEFAULT_RUNTIME,
    setRuntime: async (runtime) => {
      await saveRuntime.mutateAsync(runtime);
    },
    isSavingRuntime: saveRuntime.isPending,
  };
}
