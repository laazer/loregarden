import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { launchIsLive, reviewApi, type LaunchPhase, type ReviewBuild } from "../../api/reviewApi";
import { usePolledReadFailure } from "../../hooks/usePolledReadFailure";
import { launchStatusKey, useLaunchBuild } from "../../hooks/useReviewLaunch";
import type { StatusTone } from "../../lib/statusTone";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { StaleReadNotice } from "../ui/StaleReadNotice";
import { StatusChip } from "../ui/StatusChip";

const LIVE_POLL_MS = 2000;

const PHASE_CHIP: Record<LaunchPhase, { tone: StatusTone; label: string }> = {
  preparing: { tone: "run", label: "Preparing" },
  running: { tone: "run", label: "Running" },
  exited: { tone: "idle", label: "Exited" },
  failed: { tone: "bad", label: "Failed" },
  stopped: { tone: "idle", label: "Stopped" },
};

/**
 * The one button: run the build that holds every ticket in this group.
 *
 * A workspace without `.loregarden/review.yaml` gets the reason and the file
 * to add, not a missing button — the reviewer should learn why there is
 * nothing to press. The launch keeps the pressed button disabled until the
 * server answers, so a double click starts one build.
 */
export function ReviewLaunchPanel({ build }: { build: ReviewBuild }) {
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
          Add <code>{config.path}</code> with a <code>launch.command</code> (and optionally{" "}
          <code>prepare</code> and <code>scene_command</code>).
        </p>
      </div>
    );
  }

  const sameRef = state?.ref === build.ref;
  const live = launchIsLive(state) && sameRef;
  const chip = state ? PHASE_CHIP[state.phase] : null;
  return (
    <div className="review-launch">
      {staleNotice}
      <div className="review-launch-row">
        <Button
          variant="primary"
          disabled={launch.isPending}
          onClick={() => launch.mutate("")}
        >
          {launch.isPending ? "Starting…" : live ? "Restart build" : "Launch build"}
        </Button>
        {live ? (
          <Button variant="secondary" disabled={stop.isPending} onClick={() => stop.mutate()}>
            Stop
          </Button>
        ) : null}
        {chip && state ? (
          <span className="review-launch-state">
            <StatusChip tone={chip.tone} label={chip.label} />
            <span className="review-mono" title={state.sha}>
              {state.ref}
              {state.scene ? ` · ${state.scene}` : ""} @ {state.sha.slice(0, 8)}
            </span>
          </span>
        ) : (
          <span className="review-muted">Builds {build.ref} in a review checkout.</span>
        )}
      </div>
      {state?.error ? (
        <p className="review-error" role="alert">
          {state.error}
        </p>
      ) : null}
      {state && state.log_tail.length > 0 ? (
        <details className="review-log">
          <summary>Build log (last {state.log_tail.length} lines)</summary>
          <pre>{state.log_tail.join("\n")}</pre>
        </details>
      ) : null}
    </div>
  );
}

