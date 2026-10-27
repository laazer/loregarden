import { Link } from "react-router-dom";

import type { Approval, SurveyCard } from "../api/chatTypes";
import { memoryPath } from "../lib/appNavigation";
import { NOTHING_MORE, useReviewNow } from "./memory/useReviewNow";
import { Button } from "./ui/Button";

const FOOTER_BUTTON = { flex: 1, borderRadius: 0 } as const;

function RetryNowButton({ card, sourceId }: { card: SurveyCard; sourceId: string }) {
  const review = useReviewNow(sourceId, card.source_name || "the source");
  return (
    <Button
      variant="secondary"
      style={{ ...FOOTER_BUTTON, color: "var(--grl)" }}
      // Held: a pass is reading the source, or it is not admitted; the note says which.
      disabled={review.isPending || Boolean(card.retry_held)}
      onClick={() => review.mutate(NOTHING_MORE)}
    >
      {review.isPending ? "Retrying…" : "Retry now"}
    </Button>
  );
}

/**
 * The footer of a card whose Approve and Reject would only close it (the
 * server's `close_only`): offer what exists instead. A survey card gets Retry
 * now where `review_now` can act and a link to its source; every such card
 * gets Dismiss, which closes it without claiming a decision.
 */
export function CloseOnlyActions({
  approval,
  isSubmitting,
  dismissOpensDialog,
  dismissDialogOpen,
  onDismiss,
}: {
  approval: Approval;
  isSubmitting?: boolean;
  /** Dismiss asks for a reason in a dialog (the inbox's Ignore flow). */
  dismissOpensDialog: boolean;
  dismissDialogOpen: boolean;
  onDismiss: () => void;
}) {
  const card = approval.survey_card ?? null;
  const sourceId = card?.source_id ?? null;
  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%" }}>
      {card?.retryable && card.retry_held ? (
        <p
          role="note"
          style={{ margin: 0, padding: "6px 12px", fontSize: "var(--fs-small)", color: "var(--txm)" }}
        >
          Retry is held: {card.retry_held}
        </p>
      ) : null}
      <div style={{ display: "flex" }}>
        {card?.retryable && sourceId ? <RetryNowButton card={card} sourceId={sourceId} /> : null}
        {sourceId ? (
          <Link
            className="btn-secondary"
            style={{ ...FOOTER_BUTTON, textAlign: "center" }}
            to={memoryPath("library", sourceId)}
          >
            Open source
          </Link>
        ) : null}
        <Button
          variant="secondary"
          style={FOOTER_BUTTON}
          disabled={isSubmitting}
          aria-haspopup={dismissOpensDialog ? "dialog" : undefined}
          aria-expanded={dismissOpensDialog ? dismissDialogOpen : undefined}
          onClick={onDismiss}
        >
          Dismiss
        </Button>
      </div>
    </div>
  );
}
