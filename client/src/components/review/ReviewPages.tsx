import { useMutation, useQueryClient, type UseMutationResult, type UseQueryResult } from "@tanstack/react-query";
import { useId } from "react";

import { reviewApi, type CaptureResult, type PreviewView, type ReviewLink } from "../../api/reviewApi";
import { REVIEW_QUEUE_KEY } from "../../hooks/useReviewLaunch";
import { Button } from "../ui/Button";
import { StatusChip } from "../ui/StatusChip";

/**
 * "Which pages does this change touch, and can I see them right now?"
 *
 * A kit's path links are opened on the instance that runs the build: a client
 * whose checkout is the build's branch. With none running, Start preview
 * launches one (loregarden's own server and client); Capture screenshots every
 * linked page on it and files the pictures in the kit above.
 */
export function ReviewPages({
  links,
  workspaceSlug,
  buildRef,
  ticketId,
  preview,
  start,
}: {
  links: ReviewLink[];
  workspaceSlug: string;
  buildRef: string;
  ticketId: string;
  preview: UseQueryResult<PreviewView>;
  start: UseMutationResult<PreviewView, Error, void>;
}) {
  const titleId = useId();
  const qc = useQueryClient();
  const paths = links.filter((link) => link.path);
  const urls = links.filter((link) => link.url);
  const client = preview.data?.client ?? null;
  const ready = client?.state === "ready" ? client : null;
  const capture = useMutation({
    meta: { errorTitle: `Capture ${paths.length} page(s) of ${buildRef}` },
    mutationFn: () =>
      reviewApi.capture(workspaceSlug, { ref: buildRef, ticket_id: ticketId, paths: paths.map((p) => p.path) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: REVIEW_QUEUE_KEY }),
  });

  if (links.length === 0) return null;
  return (
    <section className="review-section" aria-labelledby={titleId}>
      <h3 id={titleId} className="review-section-title">
        Pages to check ({links.length})
      </h3>
      {paths.length > 0 ? (
        <PreviewLine preview={preview} start={start} buildRef={buildRef} />
      ) : null}
      <ul className="review-links">
        {paths.map((link) => (
          <li key={`path:${link.path}`}>
            {ready ? (
              <a href={`${ready.url}${link.path}`} target="_blank" rel="noreferrer">
                {link.label || link.path}
              </a>
            ) : (
              <span>{link.label || link.path}</span>
            )}
            <span className="review-mono review-muted"> {link.path}</span>
          </li>
        ))}
        {urls.map((link) => (
          <li key={`url:${link.url}`}>
            <a href={link.url} target="_blank" rel="noreferrer">
              {link.label || link.url}
            </a>
            {link.label ? <span className="review-mono review-muted"> {link.url}</span> : null}
          </li>
        ))}
      </ul>
      {paths.length > 0 ? (
        <div className="review-capture-row">
          <Button
            variant="secondary"
            compact
            disabled={!ready || capture.isPending}
            onClick={() => capture.mutate()}
          >
            {capture.isPending ? `Capturing ${paths.length} page(s)…` : `Capture ${paths.length} page(s)`}
          </Button>
          {!ready ? <span className="review-muted">Capture needs the build running.</span> : null}
          {capture.data ? <CaptureOutcome result={capture.data} /> : null}
        </div>
      ) : null}
    </section>
  );
}

function PreviewLine({
  preview,
  start,
  buildRef,
}: {
  preview: UseQueryResult<PreviewView>;
  start: UseMutationResult<PreviewView, Error, void>;
  buildRef: string;
}) {
  if (preview.isPending) return <p className="review-muted">Looking for a running instance of {buildRef}…</p>;
  if (preview.isError) {
    return (
      <p className="review-error" role="alert">
        Could not check which instance runs {buildRef}.{" "}
        <Button variant="plain" compact onClick={() => void preview.refetch()}>
          Try again
        </Button>
      </p>
    );
  }
  const { client, can_start: canStart, unavailable } = preview.data;
  if (client?.state === "ready") {
    return (
      <p className="review-muted">
        Opens on <span className="review-mono">{client.url}</span>, running {buildRef}.
      </p>
    );
  }
  if (client?.state === "starting" || start.isPending) {
    return (
      <p className="review-preview-line" role="status">
        <StatusChip tone="idle" label="Starting" /> A preview of {buildRef} is starting; the links open once it is
        ready (a minute or two on a fresh checkout).
      </p>
    );
  }
  return (
    <p className="review-preview-line">
      No instance of <span className="review-mono">{buildRef}</span> is running.{" "}
      {canStart ? (
        <Button variant="primary" compact onClick={() => start.mutate()}>
          Start preview
        </Button>
      ) : (
        <span className="review-muted">{unavailable}</span>
      )}
    </p>
  );
}

function CaptureOutcome({ result }: { result: CaptureResult }) {
  const missed = result.pages.filter((page) => !page.captured);
  const caveats = result.pages.filter((page) => page.captured && page.note);
  const done = result.pages.length - missed.length;
  return (
    <div role="status">
      <span className="review-muted">
        {done} of {result.pages.length} captured from {result.base_url}; they are in the screenshots above.
      </span>
      {missed.length > 0 || caveats.length > 0 ? (
        <ul className="review-capture-notes">
          {missed.map((page) => (
            <li key={page.path} className="review-error">
              <span className="review-mono">{page.path}</span> not captured: {page.note}
            </li>
          ))}
          {caveats.map((page) => (
            <li key={page.path} className="review-muted">
              <span className="review-mono">{page.path}</span>: {page.note}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
