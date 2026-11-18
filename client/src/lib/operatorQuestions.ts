import type { Approval } from "../api/client";

/** What the agent said alongside an operator question (`tool_input_json.operator_question`). */
export type OperatorQuestionMeta = {
  default: string;
  confidence: "low" | "medium" | "high";
  reversibility: "easy" | "costly" | "irreversible";
  affects: string;
  asked_at_stage: string;
  context: string;
};

/** The agent's stated default and context, or null when the payload is not one. */
export function operatorQuestionMeta(approval: Approval): OperatorQuestionMeta | null {
  if (approval.kind !== "operator_question") return null;
  try {
    const payload = JSON.parse(approval.tool_input_json || "{}") as {
      operator_question?: Partial<OperatorQuestionMeta>;
    };
    const meta = payload.operator_question;
    if (!meta || typeof meta.default !== "string" || !meta.default) return null;
    return {
      default: meta.default,
      confidence: meta.confidence ?? "medium",
      reversibility: meta.reversibility ?? "easy",
      affects: meta.affects ?? "",
      asked_at_stage: meta.asked_at_stage ?? approval.stage_key ?? "",
      context: meta.context ?? "",
    };
  } catch {
    /* silent-ok: an unreadable payload renders as a plain question; the server still holds the default */
    return null;
  }
}

/** Answers preselected on the agent's default, so confirming is one click. */
export function defaultAnswers(
  approval: Approval,
  questionText: string | undefined,
): Record<string, string> {
  const meta = operatorQuestionMeta(approval);
  if (!meta || !questionText) return {};
  return { [questionText]: meta.default };
}

/**
 * True when the operator's pick differs from the agent's default, which
 * sends the `affects` stage back. A typed custom answer counts as a pick.
 */
export function isOverride(
  meta: OperatorQuestionMeta,
  answers: Record<string, string | string[]>,
  customText: Record<string, string>,
): boolean {
  const typed = Object.values(customText).find((text) => text.trim());
  const chosen = typed?.trim() ?? Object.values(answers)[0];
  return typeof chosen === "string" && chosen !== "" && chosen !== meta.default;
}
