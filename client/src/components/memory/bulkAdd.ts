import type { BulkAddItem } from "../../api/referenceLibraryApi";
import { pushToast } from "../../state/toastStore";

/** The server takes this many URLs per bulk add (`MAX_BULK_URLS`). */
export const MAX_BULK_URLS = 50;

export function announceBulk(items: BulkAddItem[]) {
  const count = (outcome: BulkAddItem["outcome"]) => items.filter((item) => item.outcome === outcome).length;
  const failed = count("failed");
  const known = count("already");
  const added = items.length - failed - known;
  const notes = [
    known ? `${known} already in the library` : "",
    failed ? `${failed} could not be added and ${failed === 1 ? "is" : "are"} left in the box, the reason beside each` : "",
  ].filter(Boolean);
  pushToast({
    tone: failed ? (added ? "warning" : "error") : "success",
    title: `Added ${added} of ${items.length} to the review queue`,
    message: notes.length ? `${notes.join("; ")}.` : "Each licence is checked before anything in it is read.",
  });
}
