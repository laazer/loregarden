import type { TicketDetail, TicketTreeNode, WorkItemType } from "../../api/client";
import type { TicketWork } from "../../hooks/useTicketWork";
import { DEFAULT_ORCHESTRATION_RUNTIME } from "../../hooks/useTicketWork";
import { stageKindLabel, stageRunButtonLabel } from "../../lib/stageDisplay";
import { canRunStage } from "../../lib/stageRunPolicy";
import { copyStageTerminalHandoffCommand } from "../../lib/terminalCommands";
import { canHaveChildren } from "../../lib/workItemHierarchy";
import { ChildTicketsList } from "../dashboard/ChildTicketsList";
import { WorkflowTicketSettings } from "../dashboard/WorkflowTicketSettings";
import { StageOverflowMenu } from "../StageOverflowMenu";
import { StageRouteHints } from "../StageRouteHints";
import { SubtreeAutopilotCard } from "../SubtreeAutopilotCard";
import { Button } from "../ui/Button";
import { runtimeSummaryLabel } from "../WorkspaceRuntimeFields";
import { WorkflowStageTimeline } from "../WorkflowStageTimeline";

type SubTicketParent = { id: string; title: string; work_item_type: WorkItemType; workspace_slug?: string };

interface TicketWorkflowColumnProps {
  ticket: TicketDetail;
  work: TicketWork;
  expandedIds: Set<string>;
  onOpenTicket: (id: string) => void;
  onToggleChild: (id: string) => void;
  onAddChild: (parent: SubTicketParent | TicketTreeNode) => void;
}

/**
 * The ticket page's left column: a parent's children and their autopilot, or a
 * leaf ticket's workflow settings and its stages, each with a run button and
 * the stage's own menu.
 */
export function TicketWorkflowColumn({
  ticket,
  work,
  expandedIds,
  onOpenTicket,
  onToggleChild,
  onAddChild,
}: TicketWorkflowColumnProps) {
  if (ticket.child_count > 0 || work.children.length > 0) {
    return (
      <div>
        {canHaveChildren(ticket.work_item_type) && ticket.work_item_type !== "initiative" ? (
          // An initiative's autopilot lives on its plan page, beside the schedule.
          <SubtreeAutopilotCard ticketId={ticket.id} workItemType={ticket.work_item_type} onOpenTicket={onOpenTicket} />
        ) : null}
        <div className="state-label workflow-lifecycle-label ticket-workflow-heading">Child tickets</div>
        <ChildTicketsList
          nodes={work.children}
          selectedId={ticket.id}
          expandedIds={expandedIds}
          onSelect={onOpenTicket}
          onToggle={onToggleChild}
          onAddChild={onAddChild}
        />
      </div>
    );
  }

  const requestStageRun = (stageKey: string) => work.setRunConfirmStageKey(stageKey);

  return (
    <>
      <WorkflowTicketSettings
        ticket={ticket}
        workflowTemplates={work.workflowTemplates.data}
        workspaceWorkflow={work.workspaceWorkflow.data}
        workflowBusy={work.workflowBusy}
        templatePending={work.setTicketTemplate.isPending}
        postureSaving={work.setCompatibilityPosture.isPending}
        onWorkflowChange={(template) => void work.requestWorkflowChange(ticket.id, template)}
        onSaveBranch={(branch) => work.saveTicketBranch(ticket.id, branch)}
        onPostureChange={(posture) => work.setCompatibilityPosture.mutate(posture)}
      />
      <div className="ticket-workflow-lifecycle">
        <div className="workflow-lifecycle-label ticket-workflow-lifecycle-head">
          <span className="state-label">Workflow lifecycle</span>
          <Button
            variant="secondary"
            compact
            onClick={() => work.setTicketModelModalOpen(true)}
            title="Model settings for this ticket's runs"
          >
            Model ·{" "}
            {runtimeSummaryLabel(ticket.orchestration_runtime ?? DEFAULT_ORCHESTRATION_RUNTIME, work.runtimeOptions.data)}
          </Button>
        </div>
        <WorkflowStageTimeline
          stages={ticket.stages}
          currentStageKey={ticket.workflow_stage_key}
          renderStageActions={(s) => {
            const runCheck = canRunStage(ticket, s);
            const isRunningThis = work.isStageRunning(s.key);
            return (
              <>
                <Button
                  variant="secondary"
                  compact
                  className="stage-run-btn"
                  disabled={!runCheck.allowed || work.workflowBusy || work.startRun.isPending}
                  title={runCheck.reason}
                  onClick={() => requestStageRun(s.key)}
                >
                  {stageRunButtonLabel(s, isRunningThis)}
                </Button>
                <StageOverflowMenu
                  ticket={ticket}
                  stage={s}
                  runCheck={runCheck}
                  isRunning={isRunningThis}
                  workflowBusy={work.workflowBusy || work.routeWorkflow.isPending || work.patchStageWorkflow.isPending}
                  onRun={requestStageRun}
                  copyTerminalPending={work.copyTerminalStageKey !== null}
                  onCopyTerminal={() => {
                    if (work.copyTerminalStageKey) return;
                    work.setCopyTerminalStageKey(s.key);
                    void copyStageTerminalHandoffCommand(ticket, s).finally(() => work.setCopyTerminalStageKey(null));
                  }}
                  onSetCursor={(stageKey) =>
                    work.patchStageWorkflow.mutate({ workflow_stage_key: stageKey, workflow_stage_status: "pending" })
                  }
                  onRouteUpstream={(fromStageKey, toStageKey, nextAgent) =>
                    work.routeWorkflow.mutate({
                      from_stage_key: fromStageKey,
                      next_stage_key: toStageKey,
                      next_agent: nextAgent,
                    })
                  }
                  onStageStatus={(stageKey, status) =>
                    work.patchStageWorkflow.mutate({ stage_key: stageKey, stage_status: status })
                  }
                  onEditState={() => work.setStateModalOpen(true)}
                />
              </>
            );
          }}
          renderStageExtras={(s) => (
            <>
              {stageKindLabel(s) ? <div className="workflow-stage-kind">{stageKindLabel(s)}</div> : null}
              <StageRouteHints stage={s} transitions={ticket.workflow_transitions ?? []} stages={ticket.stages} />
            </>
          )}
        />
      </div>
    </>
  );
}
