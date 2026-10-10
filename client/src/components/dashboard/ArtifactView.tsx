import type { ReactNode } from "react";
import type { TicketDetail } from "../../api/client";
import { InlineCodeDiffReview } from "../InlineCodeDiffReview";
import { TicketPullRequestPanel } from "./TicketPullRequestPanel";

/**
 * The Diff and PR tabs. Everything about a ticket's history — runs, errors,
 * stage reports, outputs — moved to `TicketTimeline` and `TicketOutputs`.
 */
export function ArtifactView({
  tab,
  ticket,
  onOpenEditorFile,
  onOpenPr,
  isOpeningPr = false,
  onCommitPush,
  isCommittingPush = false,
}: {
  tab: string;
  ticket?: TicketDetail;
  onOpenEditorFile?: (filePath: string) => void;
  onOpenPr?: () => void;
  isOpeningPr?: boolean;
  onCommitPush?: () => void;
  isCommittingPush?: boolean;
}) {
  if (!ticket) {
    return <div style={{ padding: 40, color: "var(--txl)", textAlign: "center" }}>No ticket selected</div>;
  }
  const art = ticket.artifacts ?? {};

  if (tab === "diff") {
    const diff = art.diff;
    if (!diff) {
      return (
        <EmptyArtifacts label="No diff captured yet">
          Shows git changes in the workspace repo (vs main) after agent runs, or when you open this tab.
        </EmptyArtifacts>
      );
    }
    return (
      <InlineCodeDiffReview
        ticketId={ticket.id}
        diff={diff}
        diffSummary={{
          files: diff.files,
          range: diff.range,
          add: diff.add,
          del: diff.del,
        }}
        onOpenEditorFile={onOpenEditorFile}
        onCommitPush={onCommitPush}
        isCommittingPush={isCommittingPush}
      />
    );
  }

  if (tab === "pr") {
    return (
      <TicketPullRequestPanel
        ticketId={ticket.id}
        onOpenPr={onOpenPr}
        isOpeningPr={isOpeningPr}
        onCommitPush={onCommitPush}
        isCommittingPush={isCommittingPush}
      />
    );
  }

  return <EmptyArtifacts />;
}

function EmptyArtifacts({
  label = "No artifacts yet",
  children,
}: {
  label?: string;
  children?: ReactNode;
}) {
  return (
    <div style={{ height: "100%", minHeight: 340, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--txl)", gap: 12, padding: 24 }}>
      <div style={{ fontFamily: "var(--dp)", fontSize: 14, color: "var(--txm)" }}>{label}</div>
      {children ? (
        <div style={{ fontSize: 12.5, textAlign: "center", maxWidth: 320, lineHeight: 1.55 }}>{children}</div>
      ) : (
        label === "No artifacts yet" && (
          <div style={{ fontSize: 12.5, textAlign: "center", maxWidth: 280 }}>Artifacts appear when agent runs complete</div>
        )
      )}
    </div>
  );
}
