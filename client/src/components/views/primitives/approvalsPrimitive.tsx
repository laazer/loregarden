/**
 * The pending approvals for one workspace, as a pane.
 *
 * ## The blocker this removes
 *
 * `ApprovalInboxPanel` was on `excludedPanels` because it "reads the singleton
 * uiStore and renders into a fixed drawer, so two instances would fight over one
 * open/closed flag and one drawer position". That was true of the *drawer*, and
 * the drawer is not the part anyone wants beside a run log. `inbox/ApprovalsList`
 * is the part they want: a query, a resolve mutation, and the cards — no store,
 * no route, no position of its own. Two of these panes are two lists over one
 * shared query, which is what react-query is for.
 *
 * ## No "Inspect" here, deliberately
 *
 * The drawer's cards can open the ticket they are blocking. A container has no
 * route — the same reason the run ledger primitive leaves its run-log callback
 * unwired — so this pane passes no `onInspect` and the button is *absent* rather
 * than present and inert. Resolving an approval is the thing a pane can do, and
 * it can do all of it.
 *
 * ## The five states
 *
 * 1. **Loading** — the list's own first render, with the cards absent. Approvals
 *    arrive in well under the ~200ms a skeleton earns, and a skeleton that
 *    flashes on every 5s poll is worse than nothing.
 * 2. **Empty** — two of them, and `ApprovalsList` keeps them apart: nothing
 *    pending anywhere, versus nothing pending *in this workspace*. The second
 *    names the workspace, or it reads as a broken filter.
 * 3. **Error** — the resolve mutation's refusal renders in the list, above the
 *    cards. A failed *fetch* raises the global toast, which is right here: the
 *    pane is one of several and an empty list is an honest interim state.
 * 4. **In flight** — `ApprovalCard` takes `isSubmitting` and disables its own
 *    controls, so the second click on Approve cannot resolve twice.
 * 5. **Keyboard** — every control is a real `button` inside the card; the pane
 *    adds no new affordance to reach.
 */

import { ApprovalsList } from "../../inbox/ApprovalsList";
import { definePrimitive } from "./definePrimitive";
import { workspaceScopeField } from "./workspaceScope";

type ApprovalsSettings = {
  workspaceSlug: string;
};

export const approvalsPrimitive = definePrimitive<ApprovalsSettings>({
  id: "approvals",
  displayName: "Approvals",
  icon: "⎇",
  category: "Tickets",
  containerKind: "panel",
  settingsFields: [
    workspaceScopeField(
      "Only show approvals from this workspace. Leave empty to show every workspace's.",
    ),
  ],
  parseSettings: (raw) => ({
    workspaceSlug: typeof raw.workspace_slug === "string" ? raw.workspace_slug : "",
  }),
  // No `Unconfigured` branch: unlike a ticket id, an empty workspace is a
  // meaningful setting here — "all of them" — and a pane that refused to render
  // until one was chosen would be demanding a narrowing nobody asked for.
  Component: ({ settings }) => <ApprovalsList workspaceSlug={settings.workspaceSlug} isActive />,
});
