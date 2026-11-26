import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import { captureUrl, type ReviewBuild, type ReviewItem } from "../../api/reviewApi";
import { approvalVariables, useApprovalResolution } from "../../hooks/useApprovalResolution";
import { REVIEW_QUEUE_KEY, useLaunchBuild } from "../../hooks/useReviewLaunch";
import { useReviewPreview } from "../../hooks/useReviewPreview";
import { ticketPath } from "../../lib/appNavigation";
import { checkedScene } from "../../lib/reviewScene";
import { impactWithoutCriteria } from "../../utils/approvalCriteria";
import { ApprovalCard } from "../ApprovalCard";
import { Button } from "../ui/Button";
import { ReviewCriteria } from "./ReviewCriteria";
import { ReviewGuide } from "./ReviewGuide";
import { ReviewPages } from "./ReviewPages";
import { ReviewRequests } from "./ReviewRequests";
import { ReviewShot } from "./ReviewShot";

/**
 * One ticket's review: what to open, what to check, what the agent captured,
 * and the decision. Criteria ticks are saved to the ticket as they are made
 * (with its revision, so a stale page is refused rather than overwriting a
 * newer edit), which is what lets a reviewer stop halfway and pick up later.
 */
export function ReviewItemPanel({
  build,
  item,
  onResolved,
}: {
  build: ReviewBuild;
  item: ReviewItem;
  onResolved: () => void;
}) {
  const qc = useQueryClient();
  const { ticket, kit, approval } = item;
  const play = useLaunchBuild(build, "Play");
  const scene = checkedScene(item);
  const otherScenes = (item.scenes ?? []).filter((other) => other !== scene);
  const guided = kit.steps.length > 0 || kit.nothing_to_judge !== "";
  const { status: preview, start: startPreview } = useReviewPreview(
    build.workspace_slug,
    build.ref,
    kit.links.some((link) => link.path) || kit.requests.length > 0,
  );
  const resolve = useApprovalResolution(ticket.id, () => {
    void qc.invalidateQueries({ queryKey: REVIEW_QUEUE_KEY });
    onResolved();
  });
  const check = useMutation({
    meta: { errorTitle: "Save criterion check" },
    mutationFn: (checked: string[]) =>
      api.updateTicket(ticket.id, {
        checked_acceptance_criteria: checked,
        expected_revision: ticket.revision,
      }),
    onSettled: () => qc.invalidateQueries({ queryKey: REVIEW_QUEUE_KEY }),
  });
  const toggle = (criterion: string) => {
    const next = new Set(ticket.checked_criteria);
    if (next.has(criterion)) next.delete(criterion);
    else next.add(criterion);
    check.mutate(ticket.acceptance_criteria.filter((c) => next.has(c)));
  };

  return (
    <article className="review-item" aria-labelledby={`review-item-${ticket.id}`}>
      <header className="review-item-head">
        <h2 id={`review-item-${ticket.id}`} className="review-item-title">
          <Link to={ticketPath(ticket.id)}>{ticket.title}</Link>
        </h2>
        <span className="review-mono review-muted">
          {ticket.external_id} · {approval.stage_name || approval.stage_key}
        </span>
      </header>

      <ReviewGuide build={build} item={item} play={play} />

      {kit.screenshots.length > 0 ? (
        <section className="review-section" aria-label="Screenshots">
          <h3 className="review-section-title">Screenshots ({kit.screenshots.length})</h3>
          <ul className="review-shots">
            {kit.screenshots.map((shot) => {
              const src = captureUrl(ticket.id, shot.capture_id);
              return <ReviewShot key={src} src={src} caption={shot.caption} scene={shot.scene} />;
            })}
          </ul>
        </section>
      ) : null}

      <ReviewPages
        links={kit.links}
        workspaceSlug={build.workspace_slug}
        buildRef={build.ref}
        ticketId={ticket.id}
        preview={preview}
        start={startPreview}
      />

      <ReviewRequests
        requests={kit.requests}
        workspaceSlug={build.workspace_slug}
        buildServerUrl={preview.data?.server?.state === "ready" ? preview.data.server.url : undefined}
      />

      {otherScenes.length > 0 ? (
        <section className="review-section" aria-label="Scenes to open">
          <h3 className="review-section-title">
            {scene ? "Other scenes this ticket changes" : "Scenes this ticket changes"}
          </h3>
          <ul className="review-scenes">
            {otherScenes.map((other) => (
              <li key={other}>
                <code className="review-mono">{other}</code>
                <Button
                  variant="secondary"
                  compact
                  disabled={play.isPending}
                  aria-label={`Open scene ${other}`}
                  onClick={() => play.mutate({ scene: other })}
                >
                  Open scene
                </Button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <ReviewCriteria ticket={ticket} saving={check.isPending} onToggle={toggle} />

      {kit.unreadable.length > 0 ? (
        <p className="review-error" role="alert">
          {kit.unreadable.length} attached review kit(s) could not be read; open the ticket's outputs to see them.
        </p>
      ) : null}

      <ApprovalCard
        // The generated checklist says "open the scene and play it"; the guide above says what to judge.
        approval={guided ? { ...approval, checklist: [] } : approval}
        impactText={ticket.acceptance_criteria.length > 0 ? impactWithoutCriteria(approval.impact) : undefined}
        clampBrief
        showWorkspace={false}
        isSubmitting={resolve.isPending}
        onApprove={(payload) => resolve.mutate(approvalVariables(approval, "approve", payload))}
        onRecheck={(payload) => resolve.mutate(approvalVariables(approval, "recheck", payload))}
        onReject={(payload) => resolve.mutate(approvalVariables(approval, "reject", payload))}
        onIgnore={(reason) => resolve.mutate({ id: approval.id, action: "ignore", response: reason })}
      />
    </article>
  );
}
