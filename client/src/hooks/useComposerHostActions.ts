import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import { api, type Approval } from "../api/client";
import type { ChatSession } from "../lib/chatSession";
import { navigateToTicket } from "../lib/useAppNavigation";
import { describeError, pushToast } from "../state/toastStore";
import type { ChatArchive } from "./useActiveChatSession";
import type { ComposerCommandActions } from "./useComposerCommands";

/**
 * Wire the control-plane `/` builtins to the APIs and navigation the current
 * screen already has. Missing prerequisites omit the callback so the menu
 * hides the command rather than offering a dead one.
 */
export function useComposerHostActions({
  workspaceSlug,
  ticketId,
  pendingApprovals,
  archive,
  session,
  onBtw,
  onAfterNewChat,
}: {
  workspaceSlug: string;
  ticketId: string | null;
  pendingApprovals: Approval[];
  archive: ChatArchive | null;
  session: ChatSession | null;
  onBtw?: (message: string) => void;
  onAfterNewChat?: () => void;
}): ComposerCommandActions {
  const qc = useQueryClient();

  const orchestrate = useMutation({
    meta: { errorTitle: "Orchestrate" },
    mutationFn: (id: string) => api.orchestrate(id, {}),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["ticket", id] });
      pushToast({
        title: "Orchestration started",
        message: "The ticket's workflow is running.",
        tone: "success",
      });
    },
  });

  const createTicket = useMutation({
    meta: { errorTitle: "Create ticket" },
    mutationFn: (title: string) =>
      api.createTicket({
        workspace_slug: workspaceSlug,
        title,
        work_item_type: "task",
      }),
    onSuccess: (ticket) => {
      qc.invalidateQueries({ queryKey: ["tickets"] });
      navigateToTicket(ticket.id);
      pushToast({
        title: "Ticket created",
        message: ticket.external_id || ticket.title,
        tone: "success",
      });
    },
  });

  const resolveApproval = useMutation({
    meta: { errorTitle: "Resolve approval" },
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      api.resolveApproval(id, { action }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      if (ticketId) qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      if (archive?.sessionId) {
        qc.invalidateQueries({
          queryKey: ["baxter-chat-session", archive.workspaceSlug, archive.sessionId],
        });
      }
    },
  });

  const openTicket = useMutation({
    meta: { errorTitle: "Open ticket" },
    mutationFn: async (id: string) => {
      try {
        return await api.ticket(id);
      } catch {
        if (!workspaceSlug) throw new Error("No workspace to search");
        const matches = await api.tickets({ workspace: workspaceSlug });
        const hit = matches.find(
          (row) =>
            row.id === id || row.external_id === id || row.external_id === id.replace(/^#/, ""),
        );
        if (!hit) throw new Error(`No ticket matching “${id}”`);
        return hit;
      }
    },
    onSuccess: (ticket) => navigateToTicket(ticket.id),
    onError: (error) => {
      pushToast({
        title: "Open ticket",
        message: describeError(error, "Ticket not found"),
        tone: "error",
      });
    },
  });

  const firstPendingId = pendingApprovals[0]?.id ?? null;
  const canStop = Boolean(session?.isBusy && session.stop);
  const stop = session?.stop;
  const startNewChat = archive?.startNewChat;
  const forkSession = archive?.forkSession;
  const sessionId = archive?.sessionId ?? "";

  const onNewChat = useCallback(() => {
    startNewChat?.();
    onAfterNewChat?.();
  }, [startNewChat, onAfterNewChat]);

  const onFork = useCallback(
    (body: string) => {
      void forkSession?.(body).catch(() => undefined);
      onAfterNewChat?.();
    },
    [forkSession, onAfterNewChat],
  );

  const onOrchestrate = useCallback(() => {
    if (!ticketId) return;
    void orchestrate.mutateAsync(ticketId).catch(() => undefined);
  }, [ticketId, orchestrate]);

  const onStop = useCallback(() => {
    void stop?.().catch(() => undefined);
  }, [stop]);

  const onApprove = useCallback(() => {
    if (!firstPendingId) return;
    void resolveApproval
      .mutateAsync({ id: firstPendingId, action: "approve" })
      .catch(() => undefined);
  }, [firstPendingId, resolveApproval]);

  const onReject = useCallback(() => {
    if (!firstPendingId) return;
    void resolveApproval
      .mutateAsync({ id: firstPendingId, action: "reject" })
      .catch(() => undefined);
  }, [firstPendingId, resolveApproval]);

  const onOpenTicket = useCallback(
    (id: string) => {
      void openTicket.mutateAsync(id).catch(() => undefined);
    },
    [openTicket],
  );

  const onCreateTicket = useCallback(
    (title: string) => {
      void createTicket.mutateAsync(title).catch(() => undefined);
    },
    [createTicket],
  );

  return useMemo(() => {
    const actions: ComposerCommandActions = {};
    if (startNewChat) actions.onNewChat = onNewChat;
    if (sessionId && forkSession) actions.onFork = onFork;
    if (ticketId) actions.onOrchestrate = onOrchestrate;
    if (canStop && stop) actions.onStop = onStop;
    if (firstPendingId) {
      actions.onApprove = onApprove;
      actions.onReject = onReject;
    }
    if (ticketId && onBtw) actions.onBtw = onBtw;
    if (workspaceSlug) {
      actions.onOpenTicket = onOpenTicket;
      actions.onCreateTicket = onCreateTicket;
    }
    return actions;
  }, [
    startNewChat,
    sessionId,
    forkSession,
    ticketId,
    canStop,
    stop,
    firstPendingId,
    onBtw,
    workspaceSlug,
    onNewChat,
    onFork,
    onOrchestrate,
    onStop,
    onApprove,
    onReject,
    onOpenTicket,
    onCreateTicket,
  ]);
}
