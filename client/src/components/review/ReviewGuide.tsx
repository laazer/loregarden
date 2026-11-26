import { useQuery, type UseMutationResult } from "@tanstack/react-query";

import { launchIsLive, reviewApi, type LaunchState, type ReviewBuild, type ReviewItem } from "../../api/reviewApi";
import { launchStatusKey, type LaunchChoice } from "../../hooks/useReviewLaunch";
import { checkedScene, sceneName } from "../../lib/reviewScene";
import { Button } from "../ui/Button";
import { LaunchPhaseChip } from "./LaunchPhaseChip";
import { WhyYou } from "./ReviewCriteria";

/**
 * "What do I, the person, actually do here?"
 *
 * The button that runs the right thing, the steps to do in it, and what each
 * answer means for the decision below. The criteria are written for the agents
 * that built and tested the change; most cannot be judged by playing it, so
 * they are not the instructions.
 */
export function ReviewGuide({
  build,
  item,
  play,
}: {
  build: ReviewBuild;
  item: ReviewItem;
  play: UseMutationResult<LaunchState, Error, LaunchChoice>;
}) {
  const { kit, launch, scenes, ticket } = item;
  const titleId = `review-guide-${ticket.id}`;
  const scene = checkedScene(item);
  const opensPages = kit.links.length > 0 || kit.requests.length > 0;
  return (
    <section className="review-guide" aria-labelledby={titleId}>
      <h3 id={titleId} className="review-guide-title">
        What to do
      </h3>
      {scene ? <PlayRow build={build} scene={scene} play={play} /> : null}
      {kit.steps.length > 0 ? (
        <>
          {!scene && !opensPages && launch.match !== "no_match" ? (
            <p className="review-muted">Start it with the button at the top of this build, then:</p>
          ) : null}
          <ol className="review-steps">
            {kit.steps.map((step) => (
              <li key={`${step.action}\n${step.expect}`}>
                <span className="review-step-action">{step.action}</span>
                <span>
                  <span className="review-step-label">Expect</span> {step.expect}
                </span>
                {step.wrong_if ? (
                  <span>
                    <span className="review-step-label review-step-label--wrong">Wrong if</span> {step.wrong_if}
                  </span>
                ) : null}
                <WhyYou judges={step.judges} reason={step.why_person} />
              </li>
            ))}
          </ol>
          <p className="review-muted">
            Every step as expected: <strong>Approve</strong>. Anything else: <strong>Reject</strong> and say which step
            and what you saw. A step an agent could check, or that you cannot do, is also a reason to reject.
          </p>
        </>
      ) : kit.nothing_to_judge ? (
        <p>
          <strong>Nothing here needs your judgment.</strong> {kit.nothing_to_judge}
        </p>
      ) : (
        <p>
          No steps were written for this ticket, so nothing says what you are asked to judge.{" "}
          {kit.screenshots.length > 0 && !opensPages
            ? "The screenshot captions below are all there is. "
            : opensPages
              ? "The pages below are all there is. "
              : ""}
          If you cannot tell, <strong>Reject</strong> and ask for review steps.
        </p>
      )}
      {scenes === null && !scene ? (
        <p className="review-muted">Could not read which scenes this ticket changes; launch the build and look around.</p>
      ) : null}
    </section>
  );
}

function PlayRow({
  build,
  scene,
  play,
}: {
  build: ReviewBuild;
  scene: string;
  play: UseMutationResult<LaunchState, Error, LaunchChoice>;
}) {
  // The launch panel above polls this key and reports a failed read; this only
  // shows the state beside the button that changed it.
  const status = useQuery({
    queryKey: launchStatusKey(build.workspace_slug),
    queryFn: () => reviewApi.launchStatus(build.workspace_slug),
  });
  if (status.data && !status.data.config.configured) return null;
  const state = status.data?.state;
  const mine = state && state.ref === build.ref && state.scene === scene ? state : null;
  const verb = launchIsLive(mine) ? "Restart" : "Play";
  return (
    <div className="review-launch-row">
      <Button variant="primary" disabled={play.isPending} onClick={() => play.mutate({ scene })}>
        {play.isPending ? "Starting…" : `${verb} ${sceneName(scene)}`}
      </Button>
      {mine ? <LaunchPhaseChip state={mine} /> : null}
      <code className="review-mono review-muted">{scene}</code>
    </div>
  );
}
