/**
 * Whether the surface a primitive is drawn on has somewhere to navigate to.
 *
 * Every `Open …` control in `ResourceActionButton` leaves the current screen:
 * it pushes a route, or it sets an editor/branch-triage target and then pushes
 * one. In a chat thread that is the right thing — the thread is a conversation
 * about work that lives elsewhere, and following a card to it is the point.
 *
 * In a Flex View pane (557) it is not. A pane is one cell of a workspace the
 * operator composed, on a `/view/:viewId` route; a control inside it that
 * navigates the app away tears down every other pane in the view to show one
 * ticket. That is a defect, not a preference, and it is the same reason 436's
 * `runLedgerPrimitive` deliberately left the panel's run-log callback unwired:
 * "opening a run log is page navigation, and a container has no route".
 *
 * So the decision is the *surface's*, not each card's, and it is carried as
 * context rather than threaded through twenty-three components as a prop.
 *
 * The default is `true` — a chat thread, and every existing test of one, keeps
 * exactly the behaviour it had. Only a consumer that has no route to offer says
 * so, and `views/primitives/chatPanePrimitive` is the only one that does.
 *
 * A `.ts` leaf with no component in it, so both sides may import it without
 * either pulling the other's module graph in.
 */

import { createContext, useContext } from "react";

export const ResourceNavigationContext = createContext(true);

/** True when an `Open …` control has a screen to open. */
export function useResourceNavigation(): boolean {
  return useContext(ResourceNavigationContext);
}
