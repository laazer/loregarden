import type { StageStatus, TicketDetail, WorkflowStageView } from "../api/types";
import { buildStageMenuActions } from "../lib/workflowStageActions";
import { OverflowMenu, OverflowMenuItem, OverflowMenuSection } from "./OverflowMenu";

interface StageOverflowMenuProps {
  ticket: TicketDetail;
  stage: WorkflowStageView;
  runCheck: { allowed: boolean; reason: string };
  isRunning: boolean;
  workflowBusy: boolean;
  onRun: (stageKey: string) => void;
  onCopyTerminal: () => void;
  onSetCursor: (stageKey: string) => void;
  onRouteUpstream: (fromStageKey: string, toStageKey: string, nextAgent?: string) => void;
  onStageStatus: (stageKey: string, status: StageStatus) => void;
  onEditState: () => void;
}

const SECTION_LABELS: Record<string, string> = {
  run: "Run",
  route: "Route",
  cursor: "Cursor",
  status: "Status",
  edit: "More",
};

export function StageOverflowMenu({
  ticket,
  stage,
  runCheck,
  isRunning,
  workflowBusy,
  onRun,
  onCopyTerminal,
  onSetCursor,
  onRouteUpstream,
  onStageStatus,
  onEditState,
}: StageOverflowMenuProps) {
  const actions = buildStageMenuActions({
    ticket,
    stage,
    runCheck,
    isRunning,
    workflowBusy,
    canCopyTerminal: true,
  });

  let lastSection: string | undefined;

  return (
    <OverflowMenu label={`${stage.name} actions`} align="right">
      {actions.map((action) => {
        const section = action.section;
        const showSection = section && section !== lastSection;
        if (section) {
          lastSection = section;
        }

        const handleSelect = () => {
          switch (action.kind) {
            case "run":
              onRun(stage.key);
              break;
            case "copy-terminal":
              onCopyTerminal();
              break;
            case "set-cursor":
              if (action.payload?.targetStageKey) {
                onSetCursor(action.payload.targetStageKey);
              }
              break;
            case "route-upstream":
              if (action.payload?.targetStageKey) {
                onRouteUpstream(stage.key, action.payload.targetStageKey, action.payload.nextAgent);
              }
              break;
            case "mark-done":
            case "reset-pending":
            case "mark-wont-do":
              if (action.payload?.status) {
                onStageStatus(stage.key, action.payload.status);
              }
              break;
            case "edit-state":
              onEditState();
              break;
          }
        };

        return (
          <div key={action.id}>
            {showSection && section ? <OverflowMenuSection title={SECTION_LABELS[section] ?? section} /> : null}
            <OverflowMenuItem
              disabled={action.disabled}
              title={action.title}
              danger={action.danger}
              onSelect={handleSelect}
            >
              {action.label}
            </OverflowMenuItem>
          </div>
        );
      })}
    </OverflowMenu>
  );
}
