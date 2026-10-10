import type { MouseEvent } from "react";

/**
 * A row's link keeps focus after it navigates — clicking it does not blur it
 * the way clicking away does — and the rail reads focus as a reason to stay
 * expanded (see `AppSidebar`'s `focusWithin`). Left alone, that pins the rail
 * open until something else on the page steals focus, well after the row that
 * caused it is gone. Blurring on click is scoped to just the row link: the
 * footer's own controls (the pin menu, in particular) still rely on focus to
 * stay open while a keyboard user is working through them.
 */
export function blurOnClick(event: MouseEvent<HTMLAnchorElement>) {
  event.currentTarget.blur();
}
