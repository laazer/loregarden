import { pushToast } from "../state/toastStore";

/**
 * Say when a start became a wait.
 *
 * The execution slots are shared with the queue board, so a full pool turns
 * "run this" into "queued behind two others" — and the ticket that comes back
 * looks the same either way. Silence here would read as nothing happening,
 * which is exactly the confusion that made the board look broken.
 */
export function notifyIfQueued(detail: { admission?: { admitted: boolean; message: string } | null }) {
  const admission = detail?.admission;
  if (!admission || admission.admitted) return;
  pushToast({
    tone: "info",
    title: "Queued — all slots busy",
    message: admission.message,
  });
}
