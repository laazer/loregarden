import type { TicketDetail } from "../../api/client";
import type { TicketTreeNode } from "../../api/types";
import type { ArtifactTab } from "../../lib/appNavigation";
import { ApprovalsView } from "./ApprovalsView";
import { ArtifactView } from "./ArtifactView";
import { HiveSimulationPanel } from "./HiveSimulationPanel";
import { TicketOutputs } from "./TicketOutputs";
import { TicketTimeline } from "./TicketTimeline";
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
  hasActiveRun,
  pendingApprovals,
  childTickets,
  selectedId,
  activeWorkspaceSlug,
  isOpeningPr,
  isCommittingPush,
  onOpenRunLog,
  onOpenTicket,
  onOpenEditorFile,
  onOpenPr,
  onCommitPush,
}: {
  artifactTab: ArtifactTab;
  ticket?: TicketDetail;
  hasActiveRun: boolean;
  pendingApprovals: number;
  childTickets: readonly TicketTreeNode[];
  selectedId: string | null;
  activeWorkspaceSlug: string;
  isOpeningPr: boolean;
  isCommittingPush: boolean;
  onOpenRunLog: (runId: string) => void;
  onOpenTicket: (ticketId: string) => void;
  onOpenEditorFile: (workspaceSlug: string, filePath: string) => void;
  onOpenPr?: () => void;
  onCommitPush?: () => void;
}) {
  // Before the ticket guards: a finding is about a ticket the reader has no
  // reason to have selected, which is exactly why this view exists.
  if (artifactTab === "monitor") return <WorkflowMonitorView ticketId={selectedId} />;

  const isActive = hasActiveRun || ticket?.workflow_stage_status === "running";

  if (artifactTab === "timeline" && ticket) {
    return (
      <TicketTimeline
        ticket={ticket}
        isActive={isActive}
        pendingApprovals={pendingApprovals}
        childTickets={childTickets}
        onOpenRunLog={onOpenRunLog}
        onOpenTicket={onOpenTicket}
      />
    );
  }

  if (artifactTab === "outputs" && ticket) return <TicketOutputs ticketId={ticket.id} isActive={isActive} />;

  if (artifactTab === "hive" && ticket) return <HiveSimulationPanel ticket={ticket} />;

  if (artifactTab === "approvals") return <ApprovalsView ticket={ticket} />;

  return (
    <ArtifactView
      tab={artifactTab}
      ticket={ticket}
      onOpenEditorFile={(filePath) =>
        onOpenEditorFile(ticket?.workspace_slug ?? activeWorkspaceSlug, filePath)
      }
      onOpenPr={selectedId ? onOpenPr : undefined}
      isOpeningPr={isOpeningPr}
      onCommitPush={selectedId ? onCommitPush : undefined}
      isCommittingPush={isCommittingPush}
    />
  );
}
