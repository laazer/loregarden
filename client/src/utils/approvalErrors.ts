export function formatApprovalResolveError(error: unknown): string {
  if (!(error instanceof Error)) return "Failed to resolve approval";
  try {
    const parsed = JSON.parse(error.message) as { detail?: string };
    return parsed.detail ?? error.message;
  } catch {
    return error.message || "Failed to resolve approval";
  }
}
