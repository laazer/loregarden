import type { HumanRequiredExitAction } from "../api/chatTypes";

/**
 * Category badges for workflow-gate exit actions.
 * Mapped from reason_code (requirement.kind only as judgment fallback) — never from impact prose.
 */
export function exitActionCategoryLabel(
  action: Pick<HumanRequiredExitAction, "reason_code" | "requirement">,
): string {
  switch (action.reason_code) {
    case "operator_judgment_required":
      return "Needs operator judgment";
    case "authority_grant_required":
    case "authority_denied":
      return "Missing authority";
    case "authority_status_unknown":
      return "Authority status unavailable";
    case "credential_unavailable":
      return "Credential unavailable";
    case "credential_status_unknown":
      return "Credential status unavailable";
    case "capability_unavailable":
      return "Capability unavailable";
    case "capability_status_unknown":
      return "Capability status unavailable";
    default:
      if (action.requirement.kind === "operator_judgment") {
        return "Needs operator judgment";
      }
      return "Needs operator judgment";
  }
}
