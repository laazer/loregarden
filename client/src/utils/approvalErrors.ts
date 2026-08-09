import { errorDetail } from "./errorDetail";

const FALLBACK = "Failed to resolve approval";

export function formatApprovalResolveError(error: unknown): string {
  return errorDetail(error, FALLBACK) ?? FALLBACK;
}
