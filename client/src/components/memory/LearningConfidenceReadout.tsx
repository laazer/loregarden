/**
 * One learning's confidence and outcome ladder (lg-improved-memory-178).
 *
 * The boundary this component owns: it renders a `LearningConfidence` and a
 * ladder count per rung, both computed server-side from runs the learning was
 * surfaced into (`services/learning_outcomes.py`). It derives nothing, so a
 * change to the scoring lands without touching it. Zero observations is its
 * own state — "not observed yet" — never a 50% bar.
 */

import { OUTCOME_RUNGS, type LearningConfidence, type OutcomeRung } from "../../api/memoryApi";

const RUNG_LABELS: Record<OutcomeRung, string> = {
  clean_pass: "Clean pass",
  passed_after_autofix: "Passed after autofix",
  rerouted: "Rerouted",
  blocked: "Blocked",
};

const percent = (value: number) => `${Math.round(value * 100)}%`;

export function LearningConfidenceReadout({
  confidence,
  ladder,
}: {
  confidence: LearningConfidence;
  ladder: Record<OutcomeRung, number>;
}) {
  if (confidence.observations === 0) {
    return (
      <p className="memory-muted">
        Not observed yet. Confidence appears once a run this learning was briefed into has
        concluded.
      </p>
    );
  }
  return (
    <div className="memory-confidence">
      <p>
        <strong>{percent(confidence.mean)}</strong> over {confidence.observations} observed{" "}
        {confidence.observations === 1 ? "run" : "runs"} · lower bound{" "}
        {percent(confidence.lower_bound)} ·{" "}
        <span className={confidence.trusted ? "memory-trusted" : "memory-unproven"}>
          {confidence.trusted ? "trusted" : "unproven"}
        </span>
      </p>
      <dl className="memory-ladder">
        {OUTCOME_RUNGS.map((rung) => (
          <div key={rung} className={`memory-rung memory-rung--${rung}`}>
            <dt>{RUNG_LABELS[rung]}</dt>
            <dd>{ladder[rung]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
