import type { TicketWork } from "../../hooks/useTicketWork";
import { DEFAULT_ORCHESTRATION_RUNTIME } from "../../hooks/useTicketWork";
import { isHumanGateStage } from "../../lib/stageDisplay";
import { errorDetail } from "../../utils/errorDetail";
import { AgentsAssembleModal } from "../AgentsAssembleModal";
import { ConfirmRunStageModal } from "../ConfirmRunStageModal";
import { DeleteTicketConfirmModal } from "../DeleteTicketConfirmModal";
import { RunLogModal } from "../RunLogModal";
import { TriageModelModal } from "../TriageModelModal";
import { UpdateStateModal } from "../UpdateStateModal";
import { WorkflowReassignWarning } from "../WorkflowReassignWarning";

function formatDeleteTicketError(error: Error): string {
  return errorDetail(error, "Failed to delete ticket") ?? "Failed to delete ticket";
}

/** Every dialog a ticket's controls open; `useTicketWork` holds which is open. */
export function TicketWorkModals({ work }: { work: TicketWork }) {
  const { sel, ticketId } = work;
  const runConfirmStage = sel?.stages.find((s) => s.key === work.runConfirmStageKey) ?? null;

  return (
    <>
      <UpdateStateModal
        open={work.stateModalOpen}
        ticket={sel ?? null}
        workflowStages={work.workspaceWorkflow.data?.stages ?? []}
        isSaving={work.saveStateFromModal.isPending}
        onClose={() => work.setStateModalOpen(false)}
        onSave={(draft, original) => work.saveStateFromModal.mutateAsync({ draft, original })}
      />

      <TriageModelModal
        open={work.ticketModelModalOpen}
        runtime={sel?.orchestration_runtime ?? DEFAULT_ORCHESTRATION_RUNTIME}
        runtimeOptions={work.ticketRuntimeOptions.data}
        runtimeOptionsError={work.ticketRuntimeOptions.error}
        isSaving={work.setTicketRuntime.isPending}
        scopeLabel="Workflow"
        subtitle="Choose a provider, then pick a model for this ticket's agent runs"
        onClose={() => work.setTicketModelModalOpen(false)}
        onSave={async (runtime) => {
          await work.setTicketRuntime.mutateAsync(runtime);
        }}
      />

      <WorkflowReassignWarning
        preview={work.pendingWorkflow?.preview ?? null}
        isPending={work.setTicketTemplate.isPending}
        onCancel={() => work.setPendingWorkflow(null)}
        onConfirm={() => {
          if (!ticketId || !work.pendingWorkflow) return;
          work.setTicketTemplate.mutate({ ticketId, template: work.pendingWorkflow.template });
        }}
      />

      <ConfirmRunStageModal
        open={!!work.runConfirmStageKey}
        ticket={sel ?? null}
        stage={runConfirmStage}
        workspaceSlug={work.activeWorkspaceSlug}
        workspaceRuntime={work.activeWorkspaceRuntime}
        runtimeOptions={work.runtimeOptions.data}
        isRunning={work.startRun.isPending || work.workflowBusy}
        isSavingRuntime={work.setRuntime.isPending}
        isOpeningPr={work.openPr.isPending}
        onClose={() => work.setRunConfirmStageKey(null)}
        onConfirm={work.confirmStageRun}
        onOpenPr={
          ticketId && runConfirmStage && isHumanGateStage(runConfirmStage)
            ? () => work.openPr.mutate(ticketId)
            : undefined
        }
      />

      <AgentsAssembleModal
        open={work.assembleModalOpen}
        ticket={sel ?? null}
        workspaceRuntime={work.activeWorkspaceRuntime}
        runtimeOptions={work.runtimeOptions.data}
        stages={sel?.stages ?? []}
        isRunning={work.orchestrate.isPending}
        isSavingRuntime={work.setRuntime.isPending}
        onClose={() => work.setAssembleModalOpen(false)}
        onConfirm={work.confirmAssemble}
      />

      <DeleteTicketConfirmModal
        open={!!work.deleteTicketTarget}
        ticket={work.deleteTicketTarget}
        isDeleting={work.deleteTicket.isPending}
        error={work.deleteTicket.error ? formatDeleteTicketError(work.deleteTicket.error) : null}
        onClose={() => {
          if (work.deleteTicket.isPending) return;
          work.setDeleteTicketTarget(null);
          work.deleteTicket.reset();
        }}
        onConfirm={() => work.deleteTicketTarget && work.deleteTicket.mutate(work.deleteTicketTarget.id)}
      />

      <RunLogModal runId={work.logRunId} onClose={() => work.setLogRunId(null)} />
    </>
  );
}
