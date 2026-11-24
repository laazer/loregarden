import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Link } from "react-router-dom";

import {
  launchIsLive,
  reviewApi,
  type LaunchKind,
  type LaunchState,
  type ReviewBuild,
  type ReviewItem,
} from "../../api/reviewApi";
import { usePolledReadFailure } from "../../hooks/usePolledReadFailure";
import { launchStatusKey, useLaunchBuild } from "../../hooks/useReviewLaunch";
import { ticketPath } from "../../lib/appNavigation";
import type { StatusTone } from "../../lib/statusTone";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { StaleReadNotice } from "../ui/StaleReadNotice";
import { StatusChip } from "../ui/StatusChip";

const LIVE_POLL_MS = 2000;

function phaseChip(state: LaunchState): { tone: StatusTone; label: string } {
  switch (state.phase) {
    case "preparing":
      return { tone: "run", label: "Preparing" };
    case "running":
      return { tone: "run", label: "Running" };
    case "exited":
      // A check that exits at all passed; one that failed is `failed`.
      return state.kind === "check"
        ? { tone: "done", label: "Passed" }
        : { tone: "idle", label: "Exited" };
    case "failed":
      return { tone: "bad", label: "Failed" };
    case "stopped":
      return { tone: "idle", label: "Stopped" };
  }
}

function launchLabel(name: string, kind: LaunchKind, live: boolean): string {
  if (live) return `Restart ${name}`;
  return kind === "check" ? `Run ${name}` : `Launch ${name}`;
}

/**
 * Run what the ticket under review changes, from the build that holds it.
 *
 * A workspace can hold several things to run, so the buttons are the targets
 * whose paths the ticket touches. A ticket that touches none says so and links
 * its diff: starting some other part of the repository would look like a review
 * and show nothing. A workspace without `.loregarden/review.yaml` gets the
 * reason and the file to add, not a missing button. Every button is disabled
 * until the server answers, so a double click starts one build.
 */
export function ReviewLaunchPanel({ build, item }: { build: ReviewBuild; item: ReviewItem }) {
  const qc = useQueryClient();
  const key = launchStatusKey(build.workspace_slug);
  const status = useQuery({
    queryKey: key,
    queryFn: () => reviewApi.launchStatus(build.workspace_slug),
    refetchInterval: (query) => (launchIsLive(query.state.data?.state) ? LIVE_POLL_MS : false),
  });
  const launch = useLaunchBuild(build, "Launch build");
  const stop = useMutation({
    meta: { errorTitle: "Stop build" },
    mutationFn: () => reviewApi.stop(build.workspace_slug),
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });

  // A poll that fails while a build runs keeps the panel (and its Stop
  // button) on screen with a notice; only a first read that fails takes it.
  const failure = usePolledReadFailure(status, build.workspace_slug, status.data !== undefined);

  if (status.isPending) return <p className="review-muted">Checking how this workspace runs…</p>;
  if (failure.blocking || status.data === undefined) {
    return (
      <p className="review-error" role="alert">
        Could not read the launch config for {build.workspace_slug}:{" "}
        {describeError(failure.blocking?.error, "the server did not answer.")} Reload to try again.
      </p>
    );
  }
  const { config, state } = status.data;
  const staleNotice = failure.stale ? (
    <StaleReadNotice
      lead="The build status may be out of date — the last refresh failed."
      error={failure.stale.error}
      fallback="The server did not answer."
      testId="review-launch-refresh-failed"
    />
  ) : null;
  if (!config.configured) {
    return (
      <div className="review-launch">
        <p>
          {config.error ||
            `${build.workspace_slug} has no launch config, so there is no build to start from here.`}
        </p>
        <p className="review-muted">
          Add <code>{config.path}</code> with a <code>launch.command</code>, or{" "}
          <code>targets</code> when the repository holds several things to run.
        </p>
      </div>
    );
  }

  const sameRef = state?.ref === build.ref;
  const live = launchIsLive(state) && sameRef;
  const chip = state ? phaseChip(state) : null;
  const kinds = new Map(config.targets.map((target) => [target.name, target.kind]));
  const matched = item.launch.targets.filter((name) => kinds.has(name));
  const noMatch = item.launch.match === "no_match";
  // Unknown changes, or an offer the config has moved on from since the queue
  // was read: list everything rather than guess.
  const unsure = !noMatch && (item.launch.match !== "matched" || matched.length === 0);
  const targets = unsure ? [...kinds.keys()] : matched;
  return (
    <div className="review-launch">
      {staleNotice}
      {unsure ? (
        <p className="review-muted">
          Could not tell which files {item.ticket.external_id} changes, so everything this workspace
          runs is listed.
        </p>
      ) : null}
      {noMatch ? (
        <p>
          Nothing {build.workspace_slug} can launch runs the files {item.ticket.external_id} changes.{" "}
          <Link to={ticketPath(item.ticket.id, "diff")}>Read its diff</Link>, or add a target for
          them to <code>{config.path}</code>.
        </p>
      ) : null}
      <div className="review-launch-row">
        {targets.map((name, i) => (
          <Button
            key={name}
            variant={i === 0 ? "primary" : "secondary"}
            disabled={launch.isPending}
            onClick={() => launch.mutate({ target: name })}
          >
            {launch.isPending && launch.variables?.target === name
              ? "Starting…"
              : launchLabel(name, kinds.get(name) ?? "app", live && state?.target === name)}
          </Button>
        ))}
        {live ? (
          <Button variant="secondary" disabled={stop.isPending} onClick={() => stop.mutate()}>
            Stop
          </Button>
        ) : null}
        {chip && state ? (
          <span className="review-launch-state">
            <StatusChip tone={chip.tone} label={chip.label} />
            <span className="review-mono" title={state.sha}>
              {state.target} · {state.ref}
              {state.scene ? ` · ${state.scene}` : ""} @ {state.sha.slice(0, 8)}
            </span>
          </span>
        ) : targets.length > 0 ? (
          <span className="review-muted">Runs {build.ref} in a review checkout.</span>
        ) : null}
      </div>
      {sameRef && state.error ? (
        <p className="review-error" role="alert">
          {state.error}
        </p>
      ) : null}
      {sameRef && state.log_tail.length > 0 ? (
        <details className="review-log">
          <summary>
            {state.target} log (last {state.log_tail.length} lines)
          </summary>
          <pre>{state.log_tail.join("\n")}</pre>
        </details>
      ) : null}
    </div>
  );
}
