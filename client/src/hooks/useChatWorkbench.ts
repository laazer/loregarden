import { useCallback } from "react";

import { useUiStore } from "../state/uiStore";
import { useMediaQuery } from "./useMediaQuery";

const WORKBENCH_STORAGE_KEY = "loregarden.chat.workbenchOpen";

/** Below this the workbench overlays the thread instead of sitting beside it (ChatSideCard.css). */
export const WORKBENCH_OVERLAY_QUERY = "(max-width: 1100px)";

/**
 * Remembered per browser on a wide window. A narrow one always starts without
 * it: there it covers the thread, so a remembered "open" greeted a phone with
 * no chat at all.
 */
function initialWorkbenchOpen(): boolean {
  if (window.matchMedia?.(WORKBENCH_OVERLAY_QUERY).matches) return false;
  try {
    const saved = window.localStorage.getItem(WORKBENCH_STORAGE_KEY);
    if (saved === "1" || saved === "0") return saved === "1";
  } catch {
    /* silent-ok: storage blocked (private window); fall through to the wide-window default */
  }
  return true;
}

function rememberWorkbenchOpen(open: boolean): void {
  try {
    window.localStorage.setItem(WORKBENCH_STORAGE_KEY, open ? "1" : "0");
  } catch {
    /* silent-ok: a preference that cannot be saved still applies for this visit */
  }
}

/**
 * The chat page's workbench: whether it is open, and whether it covers the
 * thread. Shared by the page, which draws it, and the topbar, which toggles it.
 *
 * The store holds `null` until someone chooses this visit; both readers then
 * resolve the same remembered preference from the same storage and window.
 */
export function useChatWorkbench() {
  const chosen = useUiStore((s) => s.baxterWorkbenchOpen);
  const setChosen = useUiStore((s) => s.setBaxterWorkbenchOpen);
  const overlays = useMediaQuery(WORKBENCH_OVERLAY_QUERY);
  const open = chosen ?? initialWorkbenchOpen();
  const setOpen = useCallback(
    (next: boolean) => {
      setChosen(next);
      rememberWorkbenchOpen(next);
    },
    [setChosen],
  );
  return { open, overlays, setOpen };
}
