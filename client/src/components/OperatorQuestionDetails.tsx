import type { OperatorQuestionMeta } from "../lib/operatorQuestions";

const CONFIDENCE: Record<OperatorQuestionMeta["confidence"], string> = {
  low: "Low — the gate waits for your answer",
  medium: "Medium — the gate waits for your answer",
  high: "High — the gate does not wait",
};

const REVERSIBILITY: Record<OperatorQuestionMeta["reversibility"], string> = {
  easy: "Easy to undo",
  costly: "Costly to undo",
  irreversible: "Hard to undo",
};

/**
 * What the agent is proceeding on and what an override costs, above the
 * options of an operator question, so the operator can answer without opening
 * the run.
 */
export function OperatorQuestionDetails({ meta }: { meta: OperatorQuestionMeta }) {
  return (
    <dl
      style={{
        display: "grid",
        gridTemplateColumns: "max-content 1fr",
        gap: "4px 12px",
        margin: 0,
        fontSize: "var(--fs-small)",
      }}
    >
      <dt style={{ color: "var(--txm)" }}>Agent proceeding on</dt>
      <dd style={{ margin: 0, fontWeight: 600 }}>{meta.default}</dd>
      <dt style={{ color: "var(--txm)" }}>Confidence</dt>
      <dd style={{ margin: 0 }}>{CONFIDENCE[meta.confidence]}</dd>
      <dt style={{ color: "var(--txm)" }}>If you override</dt>
      <dd style={{ margin: 0 }}>
        {REVERSIBILITY[meta.reversibility]}: <span style={{ fontFamily: "var(--mono)" }}>{meta.affects}</span>{" "}
        re-runs with your answer
      </dd>
      {meta.context && (
        <>
          <dt style={{ color: "var(--txm)" }}>Why it asked</dt>
          <dd style={{ margin: 0 }}>{meta.context}</dd>
        </>
      )}
    </dl>
  );
}
