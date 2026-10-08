import type { ReactNode } from "react";
import type { TicketDetail } from "../../api/client";
import { MarkdownContent } from "../chat/MarkdownContent";
import { InlineCodeDiffReview } from "../InlineCodeDiffReview";

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
    const pr = art.pr;
    if (!pr) {
      return (
        <EmptyArtifacts label="No pull request opened">
          Open a PR from the approval step when human sign-off is required.
          {(onCommitPush || onOpenPr) && (
            <div style={{ marginTop: 16, display: "flex", gap: 8, justifyContent: "center" }}>
              {onCommitPush && (
                <button type="button" className="btn-secondary" disabled={isCommittingPush} onClick={onCommitPush}>
                  {isCommittingPush ? "Committing…" : "Commit & push"}
                </button>
              )}
              {onOpenPr && (
                <button type="button" className="btn-secondary" disabled={isOpeningPr} onClick={onOpenPr}>
                  {isOpeningPr ? "Opening PR…" : "Open PR"}
                </button>
              )}
            </div>
          )}
        </EmptyArtifacts>
      );
    }
    return (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="state-card">
          <div className="state-label">Pull request</div>
          <div style={{ fontWeight: 600, marginTop: 8 }}>{pr.title}</div>
          {pr.number && (
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
              #{pr.number} · {pr.branch}
            </div>
          )}
          <a
            href={pr.url}
            target="_blank"
            rel="noreferrer"
            style={{ display: "inline-block", marginTop: 12, color: "var(--ac2)", fontSize: 13 }}
          >
            {pr.url}
          </a>
        </div>
        {pr.body && (
          <div className="list-btn" style={{ padding: "12px 16px" }}>
            <MarkdownContent content={pr.body} normalize={false} readerTitle={`PR #${pr.number}`} />
          </div>
        )}
      </div>
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
