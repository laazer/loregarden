/**
 * lg-milestone-that-2215's frozen census baseline: every bounded scrolling
 * region in `client/src/**`, on both axes, with the MEASURED reason a keyboard
 * can reach it.
 *
 * ## Why this is a module and not part of the test that asserts it
 *
 * `scrollRegionsAreReachable.test.ts` holds the extractor and the assertions;
 * this holds the data and the vocabulary it is written in. They were one file
 * until the baseline was regenerated from the measurement, at which point the
 * file passed the 1200-line organization cap — and the cohesive piece to take
 * out is the one that grows every time a rule block is added, not the one that
 * decides what a candidate is.
 *
 * Three other instruments READ this file rather than importing it
 * (`scrollRegionsAreReachable.adversarial.test.ts`,
 * `scrollRegionsCensusTracksDetector.test.ts`,
 * `scrollRegionsFixedSitesHaveTests.test.ts`), parsing it with TypeScript's
 * own parser, so that a baseline edited to quiet one of them has to be edited
 * visibly. Keeping the array a plain literal of string literals is therefore
 * load-bearing: no computed selector, no spread, no helper call.
 *
 * ## It is a snapshot of a measurement, not a work list
 *
 * Every reason here is produced by `scrollRegionsMeasuredReachability.test.ts`,
 * which builds a `ts.Program` and resolves a component tag to its declaration
 * (AC10(d)(2)). Regenerate it from that measurement; do not hand-edit a reason
 * to make an assertion pass. The set of regions this ticket FIXES appears
 * nowhere in it — that set is computed by AC10(g1), and no criterion of the
 * ticket states a figure for it (AC10(c)).
 */

/** Why a baseline entry is reachable, or why it cannot be judged. */
export type Reason =
  /**
   * The scroll element itself carries a literal non-negative `tabIndex`.
   *
   * This is also what a FIX measures as, which is why there is no separate
   * term for one. The term that named this ticket as the fixer was deleted in
   * round 4 because it was never a measured property of anything: a fixed
   * element carries `tabIndex={0}`, so it measures `tabbable`, and a term
   * naming the ticket that did it is a claim about history that no scan of the
   * tree can check.
   */
  | "tabbable"
  /** Its subtree already holds a tab stop; `note` names it with file and line. */
  | "focusable-descendant"
  /**
   * Scrolled by a composite widget's own keys, whose driving control sits
   * OUTSIDE the region; `note` names that control with its file and line.
   *
   * Distinct from `focusable-descendant`, where the tab stop is IN the
   * subtree — which is the thing a static scan can actually measure, and so
   * the line this vocabulary draws between the two.
   */
  | "composite-widget-keys"
  /**
   * The element at this site renders a fixed placeholder, not the bounded
   * content the rule exists for: an empty state, a skeleton, a single sentence
   * or a one-line alert, holding no collection render and no control. `note`
   * names the branch. Fixing one would ship exactly the dead tab stop AC8
   * forbids — a name announced with nothing behind it.
   */
  | "placeholder-site"
  /**
   * The subtree's reachability cannot be decided statically EVEN AFTER the
   * declaration resolution AC10(d)(2) requires, and `note` must name the
   * construct that resolves to no declaration in this program.
   *
   * A component tag is NOT such a construct: the sibling census resolves a
   * tag's symbol to its declaration and keeps walking. What is left is
   * `{children}`, a bare prop-typed name, and a spread attribute. Round 3
   * measured 71 sites here, eight of them regions this ticket exists to fix;
   * resolving the tag took it to three, which is the difference between a
   * measurement and a shrug.
   */
  | "unmeasurable"
  /** Never rendered at rest; `note` says what has to happen first. */
  | "not-rendered-at-rest"
  /** The selector's class appears in no TSX `className`. */
  | "no-element"
  /** The class is built at runtime and cannot be joined to an element. */
  | "unresolved";

/**
 * The closed vocabulary — EIGHT terms, and nothing else.
 *
 * Two are deleted and may not reappear under any spelling: the one meaning
 * "unreachable, and this ticket does not fix it" went with AC-FOLD, because a
 * sweep that ships with a remainder is the first ticket of the next
 * enumeration rather than the last of this one; and the one naming this ticket
 * as the fixer went with round 4, for the reason written on `tabbable` above.
 *
 * There is deliberately NO term meaning "measured as holding no tab stop". A
 * site that measures that way has nothing to carry it, so it fails the sibling
 * census's AC10(g1) assertion rather than being recorded — which is AC-FOLD's
 * measurable form, and the only shape that three rounds of hand-maintained
 * lists could not quietly narrow.
 */
export const REASONS: readonly Reason[] = [
  "tabbable",
  "focusable-descendant",
  "composite-widget-keys",
  "placeholder-site",
  "unmeasurable",
  "not-rendered-at-rest",
  "no-element",
  "unresolved",
];

export interface BaselineEntry {
  selector: string;
  /** The reason that holds for every element site, unless `sites` overrides it. */
  reason: Reason;
  /**
   * Required for every reason that explains nothing without the thing it
   * points at named: `focusable-descendant`, `composite-widget-keys`,
   * `placeholder-site`, `unmeasurable` and `not-rendered-at-rest`. For
   * `unmeasurable` the content is required too — the construct that resolves
   * to no declaration in this program (AC10(h)).
   */
  note?: string;
  /**
   * Element sites whose measured reason differs from the block's.
   *
   * A rule block is the unit this test counts (AC10(a)), but reachability is a
   * property of an ELEMENT, and 29 of the blocks below are named by more than
   * one. `.modal-body` is named by 53 elements. Where they agree, one reason
   * covers the block; where they do not, the site says so rather than letting
   * one word stand for fifty-three different answers.
   */
  sites?: { where: string; reason: Reason; note?: string }[];
}

/**
 * Every candidate, with the reason measured for it.
 *
 * Shrink-only: add nothing here that is not a candidate, and delete an entry
 * when its rule goes. An entry added to quiet a failure is the allowlist this
 * test exists not to be.
 */
export const BASELINE: readonly BaselineEntry[] = [
  {
    selector: ".app-sidebar-list",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> components/AppSidebarRunning.tsx:88",
  },
  {
    selector: ".app-sidebar-menu",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/AppSidebar.tsx:185",
  },
  {
    selector: ".app-sidebar-pages",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <NavLink> components/appSidebarRows.tsx:171",
  },
  {
    selector: ".branch-cleanup-modal .modal-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Input> components/NewViewModal.tsx:87",
    sites: [
      {
        where: "components/DeleteViewConfirmModal.tsx:44",
        reason: "tabbable",
      },
      {
        where: "components/BranchCheckoutConfirmModal.tsx:46",
        reason: "tabbable",
      },
      {
        where: "components/DeleteTicketConfirmModal.tsx:46",
        reason: "tabbable",
      },
      {
        where: "components/initiatives/plan/RedraftConfirmModal.tsx:40",
        reason: "tabbable",
      },
      {
        where: "components/UsageModal.tsx:172",
        reason: "tabbable",
      },
      {
        where: "components/UsageModal.tsx:190",
        reason: "placeholder-site",
        note: "the loading / nothing-reported branch (UsageModal.tsx:190), one line of text with no control and no collection. It replaces the fixed region above rather than living inside it, because a tab stop announcing \"Usage by provider\" over the word \"Loading…\" is the dead stop AC8 forbids. The error line is NOT one of its states: react-query keeps stale data across a failed refetch, so it renders beside the region instead (UsageModal.tsx:165).",
      },
      {
        where: "components/studio/AgentDeleteConfirm.tsx:60",
        reason: "tabbable",
      },
      {
        where: "components/workspaces/WorkspaceFixDialog.tsx:94",
        reason: "tabbable",
      },
    ],
  },
  {
    selector: ".branch-triage-list",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/BranchTriageList.tsx:287",
  },
  {
    selector: ".branch-triage-main",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/TopbarDropdown.tsx:30",
    sites: [
      {
        where: "pages/BranchTriagePage.tsx:242",
        reason: "placeholder-site",
        note: "placeholder branch, no collection render and no control, at pages/BranchTriagePage.tsx:242",
      },
    ],
  },
  {
    selector: ".branch-worktrees-info",
    reason: "tabbable",
  },
  {
    selector: ".copilot-dock-approvals",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <ApprovalCard> buttons via <PendingApprovalsSection> components/CopilotDock.tsx",
  },
  {
    selector: ".copilot-dock-artifacts",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/chat/ChatArtifactsList.tsx:42",
  },
  {
    selector: ".copilot-dock-messages",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/chat/MarkdownContent.tsx:114",
  },
  {
    selector: ".dock-welcome",
    reason: "placeholder-site",
    note: "placeholder branch, no collection render and no control, at components/chat/DockChatWelcome.tsx:57",
    sites: [
      {
        where: "components/chat/DockChatWelcome.tsx:67",
        reason: "focusable-descendant",
        note: "tab stop in the subtree: <Button> components/chat/ChatPromptRow.tsx:31 (gated on runtime state)",
      },
    ],
  },
  {
    selector: ".local-instances-log",
    reason: "tabbable",
  },
  {
    selector: ".running-lane-tabs",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/logs/RunningLaneTabs.tsx:20",
  },
  {
    selector: ".queue-dispatch-menu",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Input> components/QueueSlotTicketPicker.tsx:138",
  },
  {
    selector: ".queue-lane-scroller",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/OverflowMenu.tsx:53 (gated on runtime state)",
  },
  {
    selector: ".queue-slot-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> components/DockerQueueBoard.tsx:251",
    sites: [
      {
        where: "components/initiatives/plan/InitiativeNow.tsx:49",
        reason: "unmeasurable",
        note: "resolves to no declaration in this program: <StatusChip> with a spread attribute, components/QueueRunningSlot.tsx:92; <StatusChip> with a spread attribute, components/QueueRunningSlot.tsx:95",
      },
    ],
  },
  {
    selector: ".prepared-action-output",
    reason: "tabbable",
  },
  {
    selector: ".queue-layout-main",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> components/DockerQueueBoard.tsx:251",
  },
  {
    selector: ".queue-rail-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/QueueHistoryRail.tsx:108 (gated on runtime state)",
  },
  {
    selector: ".diff-content",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Textarea> components/QueueDiffViewer.tsx:95 (gated on runtime state)",
  },
  {
    selector: ".modal-content",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Select> components/QueueOperationReview.tsx:209",
  },
  {
    selector: ".fanout-attempt__diff",
    reason: "tabbable",
  },
  {
    selector: ".terminal-tabs",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/TerminalWorkspace.tsx:142",
  },
  {
    selector: ".lg-chat-prompt-row",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/chat/ChatPromptRow.tsx:31 (gated on runtime state)",
  },
  {
    selector: ".chat-side-card",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/chat/workbench/WorkbenchCard.tsx:30",
  },
  {
    selector: ".lg-composer-help-list",
    reason: "tabbable",
  },
  {
    selector: ".lg-composer-menu",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/chat/ComposerCommandMenu.tsx:67",
  },
  {
    selector: ".lg-thinking-stream-body",
    reason: "tabbable",
  },
  {
    selector: ".lg-primitive-card--expanded > .lg-primitive-card-body",
    reason: "unmeasurable",
    note: "resolves to no declaration in this program: {meta} components/chat/primitives/PrimitiveCard.tsx:180; {children} components/chat/primitives/PrimitiveCard.tsx:181",
  },
  {
    selector: ".lg-primitive-edit-review",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/InlineCodeDiffReview.tsx:783",
  },
  {
    selector: ".lg-primitive-terminal-body",
    reason: "tabbable",
  },
  {
    selector: ".tl-callout-body",
    reason: "no-element",
    note: "dead CSS: no TSX names .tl-callout-body. Deleted when the rule goes, per shrink-only.",
  },
  {
    selector: ".tl-stderr",
    reason: "tabbable",
  },
  {
    selector: ".wm-error-reason",
    reason: "tabbable",
  },
  {
    selector: ".editor-tree",
    reason: "composite-widget-keys",
    note: "composite rows driven from outside: <div> components/editor/EditorFileTree.tsx:211; <div> components/editor/EditorFileTree.tsx:224",
  },
  {
    selector: ".editor-start",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Input> components/editor/EditorStartPane.tsx:92",
  },
  {
    selector: ".initiative-create-picked-list",
    reason: "tabbable",
  },
  {
    selector: ".initiative-pick-list",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/initiatives/SprintWorkControls.tsx:115 (gated on runtime state)",
  },
  {
    selector: ".initiatives-page",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> components/TopbarPageSlot.tsx:78",
  },
  {
    selector: ".plan-gantt-scroll",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/initiatives/plan/InitiativeGantt.tsx:460",
  },
  {
    selector: ".plan-main",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> pages/InitiativePlanPage.tsx:232",
    sites: [
      {
        where: "pages/InitiativePlanPage.tsx:247",
        reason: "placeholder-site",
        note: "placeholder branch, no collection render and no control, at pages/InitiativePlanPage.tsx:247",
      },
    ],
  },
  {
    selector: ".plan-pilot-critical",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/initiatives/plan/PlanTicketLink.tsx:8",
  },
  {
    selector: ".kb-panel",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <IconCloseButton> components/knowledge/KnowledgePanel.tsx:277",
  },
  {
    selector: ".mm-overview",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <summary> components/knowledge/MapOverview.tsx:133",
  },
  {
    selector: ".living-map-detail-text",
    reason: "tabbable",
  },
  {
    selector: ".disk-cleanup-list, .disk-folders-list",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Input> components/machine/DiskCleanupModal.tsx:191 (gated on runtime state)",
    sites: [
      {
        where: "components/machine/DiskCleanupModal.tsx:216",
        reason: "tabbable",
      },
      {
        where: "components/machine/DiskCleanupModal.tsx:298",
        reason: "tabbable",
      },
      {
        where: "components/machine/DiskFoldersModal.tsx:131",
        reason: "tabbable",
      },
    ],
  },
  {
    selector: ".machine-lanes",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/QueueDashboard.tsx:189 (the Queue panels tablist)",
  },
  {
    selector: ".disk-entries-scroll",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/machine/DiskEntriesTable.tsx:56",
  },
  {
    selector: ".disk-view",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/machine/DiskUsageView.tsx:146",
  },
  {
    selector: ".token-trend-view",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Select> components/machine/TokenTrendView.tsx:193",
  },
  {
    selector: ".rl-list, .rl-detail",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/memory/ReferenceLibraryTab.tsx:178",
  },
  {
    selector: ".reader-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/chat/MarkdownContent.tsx:114",
  },
  {
    selector: ".hierarchyTree",
    reason: "unresolved",
    note: "declared in HierarchyEditor.module.css, so the bundler renames the class and no literal in any TSX can be joined to it.",
  },
  {
    selector: ".hive-map__side",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/dashboard/hive/HiveAgentMap.tsx:300",
  },
  {
    selector: ".studio-record",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> components/studio/StudioEditorShell.tsx:69",
  },
  {
    selector: ".studio-index",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/studio/StudioIndex.tsx:72",
  },
  {
    selector: ".gate-row-output",
    reason: "tabbable",
  },
  {
    selector: ".gate-rail-scroll",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> components/studio/gates/GateStudioPanel.tsx:191",
  },
  {
    selector: ".tf-lines",
    reason: "tabbable",
  },
  {
    selector: ".pane-chat-primitive",
    reason: "unmeasurable",
    note: "resolves to no declaration in this program: <Chat> components/views/primitives/chatPanePrimitive.tsx:126",
  },
  {
    selector: ".pane-empty",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/views/ContainerPane.tsx:52",
  },
  {
    selector: ".chat-session-thread",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/chat/primitives/PrimitiveCard.tsx:146",
  },
  {
    selector: ".queue-lane",
    reason: "tabbable",
  },
  {
    selector: ".prereq-picker-list",
    reason: "composite-widget-keys",
    note: "composite rows driven from outside: <li> components/waiting/PrerequisitePicker.tsx:113",
  },
  {
    selector: ".gate-preset-output",
    reason: "tabbable",
  },
  {
    selector: ".workspace-setup-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/instances/WorkspaceIntegrationPanel.tsx:103 (gated on runtime state)",
  },
  {
    selector: ".app-action-bar-notes",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/chat/ComposerNotes.tsx:25",
  },
  {
    selector: ".artifact-tab-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <div> components/dashboard/WorkflowMonitorView.tsx:290",
  },
  {
    selector: ".import-selected-list",
    reason: "tabbable",
  },
  {
    selector: ".modal-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Input> components/NewViewModal.tsx:87",
    sites: [
      {
        where: "components/DeleteViewConfirmModal.tsx:44",
        reason: "tabbable",
      },
      {
        where: "components/BranchCheckoutConfirmModal.tsx:46",
        reason: "tabbable",
      },
      {
        where: "components/DeleteTicketConfirmModal.tsx:46",
        reason: "tabbable",
      },
      {
        where: "components/initiatives/plan/RedraftConfirmModal.tsx:40",
        reason: "tabbable",
      },
      {
        where: "components/UsageModal.tsx:172",
        reason: "tabbable",
      },
      {
        where: "components/UsageModal.tsx:190",
        reason: "placeholder-site",
        note: "the loading / nothing-reported branch (UsageModal.tsx:190), one line of text with no control and no collection. It replaces the fixed region above rather than living inside it, because a tab stop announcing \"Usage by provider\" over the word \"Loading…\" is the dead stop AC8 forbids. The error line is NOT one of its states: react-query keeps stale data across a failed refetch, so it renders beside the region instead (UsageModal.tsx:165).",
      },
      {
        where: "components/studio/AgentDeleteConfirm.tsx:60",
        reason: "tabbable",
      },
      {
        where: "components/workspaces/WorkspaceFixDialog.tsx:94",
        reason: "tabbable",
      },
    ],
  },
  {
    selector: ".parent-ticket-selector-tree",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <div> components/TicketTree.tsx:129",
  },
  {
    selector: ".pending-approvals",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/chat/MarkdownContent.tsx:114",
  },
  {
    selector: ".repo-path-explorer-list",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/RepoPathExplorer.tsx:94 (gated on runtime state)",
  },
  {
    selector: ".scroll-list",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/dashboard/DashboardWorkspacesPane.tsx:82",
  },
  {
    selector: ".studio-editor",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/studio/gates/GateStudioPanel.tsx:277",
  },
  {
    selector: ".studio-preview-card-body--compact",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/chat/MarkdownContent.tsx:114",
  },
  {
    selector: ".studio-preview-modal-scroll",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/chat/MarkdownContent.tsx:114",
  },
  {
    selector: ".studio-version-body",
    reason: "tabbable",
  },
  {
    selector: ".tab-bar-scroll",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/SettingsModal.tsx:171 (gated on runtime state)",
  },
  {
    selector: ".ticket-studio-chat-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Input> components/studio/TicketStudioPanel.tsx:750",
  },
  {
    selector: ".topbar-search-popover",
    reason: "composite-widget-keys",
    note: "composite rows driven from outside: <div> components/TopbarSearch.tsx:95",
  },
  {
    selector: ".baxter-chat-thread",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/chat/primitives/PrimitiveCard.tsx:146",
  },
  {
    selector: ".baxter-chat-welcome",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/chat/ChatScopeNote.tsx:42",
  },
  {
    selector: ".baxter-history-list",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> components/chat/ChatHistorySidebar.tsx:124",
  },
  {
    selector: ".home-page",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> pages/HomePage.tsx:208",
  },
  {
    selector: ".mcp-detail",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/mcp/McpServerDetail.tsx:56 (gated on runtime state)",
  },
  {
    selector: ".mcp-main",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/mcp/McpSwitchboard.tsx:90",
  },
  {
    selector: ".mcp-rules",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> components/mcp/McpRoutingRules.tsx:55",
  },
  {
    selector: ".mcp-servers-rail",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> pages/McpGatewayPage.tsx:39",
  },
  {
    selector: ".memory-node-list",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> components/memory/LearningsPanel.tsx:31",
  },
  {
    selector: ".memory-page-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <button> pages/MemoryPage.tsx:72",
  },
  {
    selector: ".wi-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Button> pages/WorkItemsPage.tsx:273 (gated on runtime state)",
  },
  {
    selector: ".wsd-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> pages/WorkspaceDashboardPage.tsx:138",
  },
  {
    selector: ".mq-log",
    reason: "tabbable",
  },
  {
    selector: ".instances-page-body",
    reason: "focusable-descendant",
    note: "tab stop in the subtree: <Link> pages/WorkspacesPage.tsx:66",
  },
];
