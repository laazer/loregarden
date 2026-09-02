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
 * ## Items and menu are separate, because some surfaces already have one
 *
 * A lane on the Queue page carries a menu of its own, and adding a second
 * trigger beside it put two `⋯` on one header — one thing, two menus, and no
 * way to tell which held what. `AddToTabItems` is the section on its own, for a
 * surface that already has somewhere to put it; `AddToTabMenu` is that plus the
 * trigger, for the five that do not.
 *
 * The split pays a second time: `OverflowMenu` renders its children only while
 * it is open, so the hook that lists the operator's tabs now runs when the menu
 * is opened rather than on every render of every page carrying the control.
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

export interface AddToTabItemsProps {
  primitiveId: string;
  /** Settings the surface already knows, as `containerWithSettings` takes them. */
  values: ReadonlyMap<string, unknown>;
  /**
   * The tab's name when this creates one — the thing being added, not the
   * primitive's type. "lg-flex-views-561" beats "Ticket" in a tab list.
   */
  title: string;
}

export interface AddToTabMenuProps extends AddToTabItemsProps {
  /** The trigger's accessible name; the surface knows what it is offering. */
  label: string;
}

/** The "add to a tab" section, to sit inside a menu the surface already has. */
export function AddToTabItems({ primitiveId, values, title }: AddToTabItemsProps) {
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
    <>
      <OverflowMenuSection title="Add to a tab" />
      <OverflowMenuItem
        disabled={isWriting}
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
          disabled={isWriting}
          onSelect={() => {
            void addToView(view.id, { primitiveId, values, title }).then(() =>
              announce(view.title),
            );
          }}
        >
          {view.title}
        </OverflowMenuItem>
      ))}
    </>
  );
}

/**
 * The same section, with a trigger of its own.
 *
 * The home check is repeated here rather than left to the items: a surface with
 * nothing to offer should render no `⋯` at all, not one that opens an empty
 * panel. It costs a registry lookup and no hooks.
 */
export function AddToTabMenu({ primitiveId, values, title, label }: AddToTabMenuProps) {
  if (getPrimitive(primitiveId) === undefined || !homeOf(primitiveId)) return null;
  return (
    <OverflowMenu label={label}>
      <AddToTabItems primitiveId={primitiveId} values={values} title={title} />
    </OverflowMenu>
  );
}
