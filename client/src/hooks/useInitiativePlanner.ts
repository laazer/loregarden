import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

import { api } from "../api/client";
import type { PlannerSnapshot } from "../api/initiativeApi";
import { describeError } from "../state/toastStore";

export function plannerQueryKey(initiativeId: string) {
  return ["initiative-planner", initiativeId] as const;
}

export function planQueryKey(initiativeId: string) {
  return ["initiatives", initiativeId, "plan"] as const;
}

/**
 * The planner conversation for one initiative.
 *
 * Same shape as the other chat surfaces: the reply lands through polling, not
 * the POST, and an optimistic user row covers the gap. When a turn settles the
 * plan is refetched too — a planner reply usually comes with a new proposal,
 * and the operator should see it without reloading.
 */
export function useInitiativePlanner(initiativeId: string) {
  const qc = useQueryClient();
  const key = useMemo(() => plannerQueryKey(initiativeId), [initiativeId]);

  const chat = useQuery({
    queryKey: key,
    queryFn: () => api.plannerChat(initiativeId),
    refetchInterval: (query) => (query.state.data?.active_turn_id ? 2000 : 15_000),
    meta: { errorTitle: "Load planner conversation" },
  });

  const busyOnServer = Boolean(chat.data?.active_turn_id);
  const previouslyBusy = useRef(busyOnServer);
  useEffect(() => {
    // A turn just settled: its proposal (if any) is on the plan now.
    if (previouslyBusy.current && !busyOnServer) {
      void qc.invalidateQueries({ queryKey: planQueryKey(initiativeId) });
    }
    previouslyBusy.current = busyOnServer;
  }, [busyOnServer, initiativeId, qc]);

  const send = useMutation({
    meta: { errorTitle: "Message the planner" },
    mutationFn: ({ content, mode }: { content: string; mode: "chat" | "draft" }) =>
      api.sendPlannerMessage(initiativeId, content, mode),
    onMutate: async ({ content, mode }) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<PlannerSnapshot>(key);
      qc.setQueryData<PlannerSnapshot>(key, (current) => ({
        initiative_id: initiativeId,
        messages: [
          ...(current?.messages ?? []),
          {
            id: `pending-${Date.now()}`,
            role: "user",
            content: content || "Draft a schedule for this initiative.",
            created_at: new Date().toISOString(),
            status: "complete",
            turn_mode: mode,
          },
        ],
        active_turn_id: current?.active_turn_id ?? "pending",
      }));
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) qc.setQueryData(key, context.previous);
    },
    onSuccess: (snapshot) => qc.setQueryData(key, snapshot),
  });

  const stop = useMutation({
    meta: { errorTitle: "Stop the planner" },
    mutationFn: () => api.stopPlannerTurn(initiativeId),
    onSuccess: (snapshot) => qc.setQueryData(key, snapshot),
    onSettled: () => {
      send.reset();
      void qc.invalidateQueries({ queryKey: key });
    },
  });

  return {
    messages: chat.data?.messages ?? [],
    activeTurnId:
      chat.data?.active_turn_id && chat.data.active_turn_id !== "pending"
        ? chat.data.active_turn_id
        : null,
    isBusy: busyOnServer || send.isPending,
    isLoading: chat.isPending,
    loadError: chat.isError ? describeError(chat.error, "Could not load the planner conversation") : null,
    retry: () => void chat.refetch(),
    sendError: send.isError ? describeError(send.error, "Could not send the message") : null,
    /** Failures toast through `meta.errorTitle`; `onError` lets the caller restore its draft. */
    send: (content: string, onError?: () => void) =>
      send.mutate({ content, mode: "chat" }, { onError }),
    draft: () => send.mutate({ content: "", mode: "draft" }),
    stop: () => stop.mutate(),
    isStopping: stop.isPending,
  };
}
