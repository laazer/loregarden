import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { isStageRunning } from "./ticketProgress";

export function useRunControls(ticketId: string | undefined) {
  const queryClient = useQueryClient();

  const invalidate = async () => {
    if (!ticketId) return;
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["ticket", ticketId] }),
      queryClient.invalidateQueries({ queryKey: ["ticket-tree"] }),
      queryClient.invalidateQueries({ queryKey: ["tickets"] }),
      queryClient.invalidateQueries({ queryKey: ["runs"] }),
    ]);
  };

  const start = useMutation({
    mutationFn: async (stageKey?: string) => {
      if (!ticketId) throw new Error("Missing ticket");
      if (stageKey) {
        return api.startRun(ticketId, { stage_key: stageKey });
      }
      return api.orchestrate(ticketId, {});
    },
    onSuccess: invalidate,
  });

  const stop = useMutation({
    mutationFn: async () => {
      if (!ticketId) throw new Error("Missing ticket");
      return api.stopTicket(ticketId);
    },
    onSuccess: invalidate,
  });

  return {
    start: (stageKey?: string) => start.mutateAsync(stageKey),
    stop: () => stop.mutateAsync(),
    isStarting: start.isPending,
    isStopping: stop.isPending,
    startError: start.error,
    stopError: stop.error,
  };
}

export function ticketIsRunning(stageStatus: string | undefined): boolean {
  return isStageRunning(stageStatus as "running" | "awaiting" | undefined);
}
