import type { TicketDetail } from "../../api/client";
import { isArtifactsSubTab, type ArtifactTab } from "../../lib/appNavigation";
import { LogsPanel } from "../LogsPanel";
import { ApprovalsView } from "./ApprovalsView";
import { ArtifactsHub } from "./ArtifactsHub";
import { ArtifactView } from "./ArtifactView";
import { HiveSimulationPanel } from "./HiveSimulationPanel";
import { WorkflowMonitorView } from "./WorkflowMonitorView";

/**
 * Which panel the artifact pane shows for the selected tab.
 *
 * Lifted out of `Dashboard`, which was 1769 lines against a 1200-line cap — the
 * cap is enforced on a net-growing file, so adding the Monitor tab there was not
 * available without this. The seam is the one that was already implicit: this is
 * the whole tab→panel switch and nothing else, and every panel below takes the
 * props it already took.
 *
 * Most tabs are about the selected ticket and render nothing useful without one.
 * `monitor` deliberately is not: it spans every ticket, which is the point of it,
 * so it is checked before the `sel` guards rather than after.
 */
export function ArtifactPaneBody({
  artifactTab,
  ticket,
  runs,
  hasActiveRun,
  hasRunErrors,
  selectedId,
  activeWorkspaceSlug,
  isOpeningPr,
  isCommittingPush,
  onOpenRunLog,
  onOpenEditorFile,
  onOpenPr,
  onCommitPush,
}: {
  artifactTab: ArtifactTab;
  ticket?: TicketDetail;
  runs: Parameters<typeof ArtifactView>[0]["runs"];
  hasActiveRun: boolean;
  hasRunErrors: boolean;
  selectedId: string | null;
  activeWorkspaceSlug: string;
  isOpeningPr: boolean;
  isCommittingPush: boolean;
  onOpenRunLog: (runId: string) => void;
  onOpenEditorFile: (workspaceSlug: string, filePath: string) => void;
  onOpenPr?: () => void;
  onCommitPush?: () => void;
}) {
  // Before the ticket guards: a finding is about a ticket the reader has no
  // reason to have selected, which is exactly why this view exists.
  if (artifactTab === "monitor") return <WorkflowMonitorView />;

  if (artifactTab === "logs" && ticket) return <LogsPanel ticket={ticket} />;

  if (isArtifactsSubTab(artifactTab) && ticket) {
    return (
      <ArtifactsHub
        ticket={ticket}
        subTab={artifactTab}
        runs={runs}
        isActive={hasActiveRun || ticket.workflow_stage_status === "running"}
        hasRunErrors={hasRunErrors}
        onOpenRunLog={onOpenRunLog}
      />
    );
  }

  if (artifactTab === "hive" && ticket) return <HiveSimulationPanel ticket={ticket} />;

  if (artifactTab === "approvals") return <ApprovalsView ticket={ticket} />;

  return (
    <ArtifactView
      tab={artifactTab}
      ticket={ticket}
      runs={runs}
      onOpenEditorFile={(filePath) =>
        onOpenEditorFile(ticket?.workspace_slug ?? activeWorkspaceSlug, filePath)
      }
      onOpenPr={selectedId ? onOpenPr : undefined}
      isOpeningPr={isOpeningPr}
      onCommitPush={selectedId ? onCommitPush : undefined}
      isCommittingPush={isCommittingPush}
      onOpenRunLog={onOpenRunLog}
    />
  );
}
