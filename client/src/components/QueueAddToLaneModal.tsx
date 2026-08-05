/**
 * Putting a ticket in a lane, through the same dialog as Assemble.
 *
 * A lane runs a whole ticket, so the settings that matter are the pipeline's —
 * where to stop, whether to auto-approve, which model — which is exactly what
 * `AgentsAssembleModal` already asks. Reusing it keeps one dialogue for "run
 * this ticket" rather than growing a second dialect of it on the board.
 *
 * What this adds is the fetching and the commit. The Dashboard has the ticket,
 * its stages and its workspace runtime in hand; the board has a ticket id and
 * nothing else, and since lanes are shared across workspaces the one to load is
 * the *ticket's*, not the page's.
 *
 * There is no Start afterwards: confirming puts the ticket in the lane, which
 * runs it if the lane is idle and queues it otherwise.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '../api/client';
import type { WorkspaceRuntimeSettings } from '../api/types';
import { queueLanesApi } from '../lib/queueLanesApi';
import { AgentsAssembleModal, type AgentsAssembleOptions } from './AgentsAssembleModal';
import { runtimeSettingsEqual } from './WorkspaceRuntimeFields';

export interface QueueAddRequest {
  ticketId: string;
  slotNumber: number;
  /** Slug of the workspace the ticket belongs to — whose runtime this edits. */
  workspaceSlug: string;
  /** Whether confirming starts it now or puts it in line. Copy only. */
  laneIsIdle: boolean;
}

interface QueueAddToLaneModalProps {
  request: QueueAddRequest | null;
  onClose: () => void;
  onError: (message: string) => void;
}

/** Until the workspace's runtime loads, show "inherit" rather than guessing. */
const RUNTIME_FALLBACK: WorkspaceRuntimeSettings = {
  cli_adapter: '',
  claude_model: '',
  cursor_model: '',
  lmstudio_base_url: '',
  lmstudio_model: '',
};

export function QueueAddToLaneModal({ request, onClose, onError }: QueueAddToLaneModalProps) {
  const qc = useQueryClient();
  const open = Boolean(request);

  const ticket = useQuery({
    queryKey: ['ticket', request?.ticketId],
    queryFn: () => api.ticket(request!.ticketId),
    enabled: open && Boolean(request?.ticketId),
  });

  const workspaceRuntime = useQuery({
    queryKey: ['workspace-runtime', request?.workspaceSlug],
    queryFn: () => api.workspaceRuntime(request!.workspaceSlug),
    enabled: open && Boolean(request?.workspaceSlug),
  });

  const runtimeOptions = useQuery({
    queryKey: ['runtime-options', request?.workspaceSlug],
    queryFn: () => api.runtimeOptions({ workspace: request!.workspaceSlug }),
    enabled: open && Boolean(request?.workspaceSlug),
  });

  const setRuntime = useMutation({
    meta: { errorTitle: 'Save runtime' },
    mutationFn: (runtime: WorkspaceRuntimeSettings) =>
      api.setWorkspaceRuntime(request!.workspaceSlug, runtime),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspace-runtime', request?.workspaceSlug] });
    },
  });

  const addToLane = useMutation({
    meta: { errorTitle: 'Add to lane' },
    mutationFn: (options: AgentsAssembleOptions) =>
      // The lane clicked is the default, not the decision: the dialog shows
      // what every lane is doing, so changing your mind there has to land here
      // rather than silently going back to the one you opened it from.
      queueLanesApi.add(options.slotNumber ?? request!.slotNumber, {
        ticket_id: request!.ticketId,
        auto_approve: options.autoApprove,
        stop_at_stage_key: options.stopAtStageKey,
      }),
  });

  const handleConfirm = async (options: AgentsAssembleOptions) => {
    if (!request) return;
    try {
      // The runtime is the workspace's, not the run's, so saving it changes
      // every future run there — same as the Dashboard, but worth being
      // deliberate about, because from this board the workspace is whichever
      // one the ticket belongs to rather than the one you were looking at.
      if (workspaceRuntime.data && !runtimeSettingsEqual(options.runtime, workspaceRuntime.data)) {
        await setRuntime.mutateAsync(options.runtime);
      }
      if (ticket.data && options.branch !== (ticket.data.branch || '')) {
        await api.updateTicket(request.ticketId, { branch: options.branch });
      }
      await addToLane.mutateAsync(options);
      onClose();
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Could not add to the lane');
    }
  };

  // Nothing to confirm until the ticket is loaded — the dialog is about a
  // specific ticket's pipeline, and it cannot name one it does not have.
  if (!open || !ticket.data) return null;

  return (
    <AgentsAssembleModal
      open
      ticket={ticket.data}
      workspaceRuntime={workspaceRuntime.data ?? RUNTIME_FALLBACK}
      runtimeOptions={runtimeOptions.data}
      stages={ticket.data.stages ?? []}
      defaultSlotNumber={request!.slotNumber}
      isRunning={addToLane.isPending}
      isSavingRuntime={setRuntime.isPending}
      onClose={onClose}
      onConfirm={handleConfirm}
    />
  );
}
