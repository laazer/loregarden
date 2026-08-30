/**
 * The "⋯ → add this to a tab" menu, on the pages a primitive already draws.
 *
 * Every container primitive is a second view of something a page here is
 * already showing. Getting one into a view meant opening a tab, adding a pane,
 * picking the primitive and typing an identifier — from the page that had the
 * identifier on screen. This is the shortcut: the surface knows what it is
 * showing, so it passes those settings straight through and the pane arrives
 * configured.
 *
 * ## Where it may appear
 *
 * Only where `primitiveHomes` says the primitive belongs. The map is what stops
 * this becoming an "add to tab" button on every panel in the app regardless of
 * whether the thing beneath it has a pane to become — and a primitive with no
 * home (`web_embed`) has no menu anywhere, which is the map saying so rather
 * than an omission.
 *
 * ## Which workspace the tab lands in
 *
 * The sidebar's, always — read here rather than passed in. Several of these
 * pages have a workspace picker of their own, and a tab filed under a workspace
 * whose tab list is not on screen is a tab the operator cannot find.
 *
 * ## What it does not do
 *
 * It does not navigate. A menu that jumped to the new tab would take an
 * operator off the page they were working on to look at a pane they can already
 * see — the point of adding it to a tab is to have it *later*. The toast is the
 * confirmation, and the sidebar's tab list is where it went.
 */

import { useAddPrimitiveToTab } from "../hooks/useAddPrimitiveToTab";
import { useSidebarWorkspaceSlug } from "../state/SidebarWorkspaceContext";
import { pushToast } from "../state/toastStore";
import { OverflowMenu, OverflowMenuItem, OverflowMenuSection } from "./OverflowMenu";
import { getPrimitive } from "./views/primitives/registry";
import { homeOf } from "./views/primitives/primitiveHomes";

export interface AddToTabMenuProps {
  primitiveId: string;
  /** Settings the surface already knows, as `containerWithSettings` takes them. */
  values: ReadonlyMap<string, unknown>;
  /**
   * The tab's name when this creates one — the thing being added, not the
   * primitive's type. "lg-flex-views-561" beats "Ticket" in a tab list.
   */
  title: string;
  /** The trigger's accessible name; the surface knows what it is offering. */
  label: string;
}

export function AddToTabMenu({ primitiveId, values, title, label }: AddToTabMenuProps) {
  // The sidebar's workspace, not the page's. Tabs are sidebar furniture and
  // belong to whatever workspace the chrome is showing; a page with a workspace
  // picker of its own — the queue, branch triage — would otherwise file a tab
  // under a workspace whose tab list the operator is not looking at.
  const slug = useSidebarWorkspaceSlug();
  const { views, addToView, addToNewView, isWriting } = useAddPrimitiveToTab(slug);
  const entry = getPrimitive(primitiveId);
  const home = homeOf(primitiveId);

  // A build without this primitive, or one the map says has no home, offers
  // nothing rather than a menu whose every item fails.
  if (entry === undefined || home === null || home === undefined) return null;

  // The confirmation, and the only one: nothing on screen changes when a pane
  // is added to a tab you are not looking at, so a silent success is
  // indistinguishable from a menu that did nothing.
  const announce = (viewTitle: string) => {
    pushToast({
      tone: "success",
      title: `${entry.displayName} added to ${viewTitle}`,
      message: "Open the tab from the sidebar to see it.",
    });
  };

  return (
    <OverflowMenu label={label} disabled={isWriting}>
      <OverflowMenuSection title="Add to a tab" />
      <OverflowMenuItem
        onSelect={() => {
          void addToNewView({ primitiveId, values, title }).then((view) =>
            announce(view.title),
          );
        }}
      >
        New tab
      </OverflowMenuItem>
      {views.length === 0 ? null : <OverflowMenuSection title="Existing" />}
      {views.map((view) => (
        <OverflowMenuItem
          key={view.id}
          onSelect={() => {
            void addToView(view.id, { primitiveId, values, title }).then(() =>
              announce(view.title),
            );
          }}
        >
          {view.title}
        </OverflowMenuItem>
      ))}
    </OverflowMenu>
  );
}
