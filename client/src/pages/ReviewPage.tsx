import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { reviewApi, type ReviewBuild } from "../api/reviewApi";
import { ReviewItemPanel } from "../components/review/ReviewItemPanel";
import { ReviewLaunchPanel } from "../components/review/ReviewLaunchPanel";
import { PageTopbar } from "../components/TopbarPageSlot";
import { Button } from "../components/ui/Button";
import { PaneSkeleton } from "../components/ui/PaneSkeleton";
import { REVIEW_QUEUE_KEY } from "../hooks/useReviewLaunch";
import { pathForPage } from "../lib/appNavigation";
import "./ReviewPage.css";

function buildKey(build: ReviewBuild): string {
  return `${build.workspace_slug}:${build.ref}`;
}

function findApproval(
  builds: ReviewBuild[],
  approvalId: string | null,
): { build: ReviewBuild; index: number } | null {
  if (!approvalId) return null;
  for (const build of builds) {
    const index = build.items.findIndex((item) => item.approval.id === approvalId);
    if (index >= 0) return { build, index };
  }
  return null;
}

/**
 * "What is waiting for me to look at it, and what do I run to look?"
 *
 * Every human gate, grouped by the build that holds it — for a milestone,
 * the integration branch its tickets landed on, so one launch covers them
 * all. The reviewer walks the group ticket by ticket: open the scene or page,
 * tick the criteria, read what the agent captured, decide. Deciding moves on
 * to the next one.
 *
 * `?workspace=` narrows to one workspace; `?build=` and `?item=` hold the
 * place so a link (or a reload) lands on the same ticket; `?approval=` names
 * the gate itself, for links from the inbox.
 */
export function ReviewPage() {
  const [params, setParams] = useSearchParams();
  const workspace = params.get("workspace") ?? "";
  const queue = useQuery({
    queryKey: [...REVIEW_QUEUE_KEY, workspace],
    queryFn: () => reviewApi.queue(workspace || undefined),
    meta: { suppressErrorToast: true },
  });

  const builds = useMemo(() => queue.data?.builds ?? [], [queue.data]);
  // `?approval=` (an inbox card's link) names the gate itself and wins over
  // the positional `build`/`item`; a gate already resolved falls through.
  const linked = findApproval(builds, params.get("approval"));
  const selected =
    linked?.build ??
    builds.find((b) => buildKey(b) === params.get("build")) ??
    builds[0] ??
    null;
  const itemCount = selected?.items.length ?? 0;
  const requested = linked?.index ?? Number(params.get("item") ?? 0);
  const index =
    itemCount === 0
      ? 0
      : Math.min(
          Math.max(0, Number.isFinite(requested) ? requested : 0),
          itemCount - 1,
        );
  const item = selected?.items[index] ?? null;

  const go = (next: { build?: ReviewBuild; item?: number }) => {
    const p = new URLSearchParams(params);
    p.delete("approval");
    if (!next.build && selected) p.set("build", buildKey(selected));
    if (next.build) p.set("build", buildKey(next.build));
    p.set("item", String(next.item ?? 0));
    setParams(p, { replace: true });
  };

  const title = workspace ? `Review · ${workspace}` : "Review";
  if (queue.isPending) {
    return (
      <div className="review-page">
        <PageTopbar title={title} />
        <PaneSkeleton variant="list" label="Loading what needs review…" />
      </div>
    );
  }
  if (queue.isError) {
    return (
      <div className="review-page">
        <PageTopbar title={title} />
        <div className="review-empty" role="alert">
          <p>Could not load the review queue. The server may be restarting.</p>
          <Button variant="secondary" onClick={() => void queue.refetch()}>
            Try again
          </Button>
        </div>
      </div>
    );
  }
  if (builds.length === 0 || selected === null) {
    return (
      <div className="review-page">
        <PageTopbar title={title} />
        <div className="review-empty">
          <p>
            Nothing is waiting on a person{workspace ? ` in ${workspace}` : ""}.
          </p>
          <p className="review-muted">
            Tickets land here when they reach a human gate, such as a playtest
            or a sign-off.
          </p>
          <Link to={pathForPage("work-items")}>See work in progress</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="review-page">
      <PageTopbar title={title} />
      <nav className="review-builds" aria-label="Review queue">
        <h2 className="review-section-title">
          {queue.data.total} waiting · {builds.length} build
          {builds.length === 1 ? "" : "s"}
        </h2>
        <ul aria-label="Builds to review">
          {builds.map((build) => {
            const current = buildKey(build) === buildKey(selected);
            return (
              <li key={buildKey(build)}>
                <Button
                  variant="plain"
                  className="review-build-button"
                  aria-current={current ? "true" : undefined}
                  onClick={() => go({ build, item: 0 })}
                >
                  <span className="review-build-name">
                    {build.workspace_slug}
                  </span>
                  <span className="review-mono review-muted">{build.ref}</span>
                  <span className="review-build-count">
                    {build.items.length}
                  </span>
                </Button>
              </li>
            );
          })}
        </ul>
        <h2 className="review-section-title review-walk-heading">
          Tickets in this build
        </h2>
        <ol className="review-walk-list" aria-label="Tickets in this build">
          {selected.items.map((entry, i) => (
            <li key={entry.approval.id}>
              <Button
                variant="plain"
                className="review-walk-item"
                aria-current={i === index ? "step" : undefined}
                onClick={() => go({ item: i })}
              >
                <span className="review-mono">{entry.ticket.external_id}</span>
                <span className="review-walk-title">{entry.ticket.title}</span>
              </Button>
            </li>
          ))}
        </ol>
      </nav>

      <main className="review-main">
        <section className="review-build-head" aria-label="Build">
          <h2 className="review-build-title">
            {selected.combined
              ? `${selected.items.length} tickets in one build`
              : `${selected.items[0]?.ticket.external_id ?? "Ticket"} on its own branch`}
          </h2>
          <ReviewLaunchPanel build={selected} />
        </section>

        <div className="review-walk-body">
          <div className="review-walk-nav">
            <Button
              variant="secondary"
              compact
              disabled={index === 0}
              onClick={() => go({ item: index - 1 })}
            >
              Previous
            </Button>
            <span className="review-muted">
              {index + 1} of {itemCount}
            </span>
            <Button
              variant="secondary"
              compact
              disabled={index >= itemCount - 1}
              onClick={() => go({ item: index + 1 })}
            >
              Next
            </Button>
          </div>
          {item ? (
            <ReviewItemPanel
              key={item.approval.id}
              build={selected}
              item={item}
              // The decided card leaves the queue, so the same index is the
              // next ticket; only the last one has to step back.
              onResolved={() => go({ item: Math.min(index, itemCount - 2) })}
            />
          ) : null}
        </div>
      </main>
    </div>
  );
}
