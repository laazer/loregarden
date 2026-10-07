import { useTicketRefStore } from "../state/ticketRefStore";
import { looksLikeTicketUuid } from "./ticketIds";

export type AppPage =
  | "home"
  | "chat"
  | "dashboard"
  | "initiatives"
  | "studio"
  | "editor"
  | "queue"
  | "branch-triage"
  | "mcp"
  | "memory"
  | "workspaces";

export type ArtifactTab =
  | "diff"
  | "logs"
  | "tests"
  | "hive"
  | "context"
  | "ledger"
  | "errors"
  | "pr"
  | "approvals"
  | "artifacts"
  /**
   * The only pane that is not about the selected ticket: every workflow-monitor
   * finding across every ticket. It lives here rather than in its own page
   * because it answers the question the artifact pane is already for — "what is
   * wrong with this pipeline" — and because a finding is *about* a ticket the
   * reader has no reason to have opened.
   */
  | "monitor";

/** Nested under the Artifacts top tab (URL segment still matches ArtifactTab). */
export type ArtifactsSubTab = "artifacts" | "errors" | "context" | "ledger";

export type StudioSection = "agents" | "workflows" | "tickets" | "gates";

/** Every routable ticket pane — includes Artifacts sub-tabs for deep links. */
export const ARTIFACT_TABS: ArtifactTab[] = [
  "diff",
  "errors",
  "logs",
  "artifacts",
  "tests",
  "hive",
  "context",
  "ledger",
  "pr",
  "approvals",
  "monitor",
];

/** Top tab bar only — errors/context/ledger live under Artifacts. */
export const PRIMARY_ARTIFACT_TABS: ArtifactTab[] = [
  "diff",
  "logs",
  "artifacts",
  "tests",
  "hive",
  "pr",
  "approvals",
  "monitor",
];

export const ARTIFACTS_SUB_TABS: ArtifactsSubTab[] = ["artifacts", "errors", "context", "ledger"];

export const ARTIFACTS_SUB_TAB_LABELS: Record<ArtifactsSubTab, string> = {
  artifacts: "Feed",
  errors: "Errors",
  context: "Context",
  ledger: "Ledger",
};

export const STUDIO_SECTIONS: StudioSection[] = ["agents", "workflows", "tickets", "gates"];

export const STUDIO_NEW_RESOURCE = "new";

const PAGE_PATHS: Record<AppPage, string> = {
  home: "/",
  chat: "/chat",
  dashboard: "/console",
  initiatives: "/initiatives",
  studio: "/studio/agents",
  editor: "/editor",
  queue: "/queue",
  "branch-triage": "/branch-triage",
  mcp: "/mcp",
  memory: "/memory",
  workspaces: "/workspaces",
};

const TICKET_PATH_RE = /^\/tickets\/([^/]+)(?:\/([^/]+))?/;
const VIEW_PATH_RE = /^\/view\/([^/]+)/;
const STUDIO_RESOURCE_PATH_RE = /^\/studio\/(agents|workflows|tickets|gates)(?:\/([^/]+))?/;

/**
 * A URL segment is whatever the address bar holds, and a bare `%` is not valid
 * percent-encoding: `decodeURIComponent` throws on it. These readers run during
 * render, so an unroutable id has to come back as a miss — `/view/%` blanking
 * the whole shell is not a 404.
 */
function decodeSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

// --- Gate Studio (lg-gate-studio-863) ---------------------------------------
// Three levels, all in the URL so a deep link renders what a click would:
// /studio/gates/:workspace, /studio/gates/:workspace/:workflow, and
// /studio/gates/:workspace/:workflow/:controlId. The workflow segment `_` means
// "workspace-wide" — the transition commands no single workflow owns.
const GATE_STUDIO_PATH_RE = /^\/studio\/gates(?:\/([^/]+))?(?:\/([^/]+))?(?:\/([^/]+))?\/?$/;

export const GATE_STUDIO_WORKSPACE_SCOPE = "_";

export interface GateStudioTarget {
  workspaceSlug: string | null;
  workflowSlug: string | null;
  controlId: string | null;
}

export function gateStudioPath(target: GateStudioTarget): string {
  if (!target.workspaceSlug) return "/studio/gates";
  const workspace = encodeURIComponent(target.workspaceSlug);
  if (!target.workflowSlug && !target.controlId) return `/studio/gates/${workspace}`;
  const workflow = target.workflowSlug
    ? encodeURIComponent(target.workflowSlug)
    : GATE_STUDIO_WORKSPACE_SCOPE;
  if (!target.controlId) return `/studio/gates/${workspace}/${workflow}`;
  // ':' separates an id's parts and is legal in a path segment, so it stays
  // readable; each part was already encoded on its own (gateControlModel).
  const control = encodeURIComponent(target.controlId).replace(/%3A/gi, ":");
  return `/studio/gates/${workspace}/${workflow}/${control}`;
}

export function gateStudioTargetFromPath(pathname: string): GateStudioTarget {
  const match = pathname.match(GATE_STUDIO_PATH_RE);
  if (!match) return { workspaceSlug: null, workflowSlug: null, controlId: null };
  const workflowSegment = match[2] ? decodeSegment(match[2]) : null;
  return {
    workspaceSlug: match[1] ? decodeSegment(match[1]) : null,
    workflowSlug: workflowSegment === GATE_STUDIO_WORKSPACE_SCOPE ? null : workflowSegment,
    controlId: match[3] ? decodeSegment(match[3]) : null,
  };
}

export function isStudioNewResource(resourceId: string | null | undefined): boolean {
  return resourceId === STUDIO_NEW_RESOURCE;
}

export function studioResourcePath(section: StudioSection, resourceId: string): string {
  return `${studioPath(section)}/${encodeURIComponent(resourceId)}`;
}

export function studioAgentPath(slug: string): string {
  return studioResourcePath("agents", slug);
}

export function studioAgentNewPath(): string {
  return studioResourcePath("agents", STUDIO_NEW_RESOURCE);
}

export function studioWorkflowPath(slug: string): string {
  return studioResourcePath("workflows", slug);
}

export function studioWorkflowNewPath(): string {
  return studioResourcePath("workflows", STUDIO_NEW_RESOURCE);
}

export function studioTicketSessionPath(sessionId: string): string {
  return studioResourcePath("tickets", sessionId);
}

export function studioTicketSessionNewPath(): string {
  return studioResourcePath("tickets", STUDIO_NEW_RESOURCE);
}

export function studioResourceFromPath(pathname: string): string | null {
  const match = pathname.match(STUDIO_RESOURCE_PATH_RE);
  if (!match?.[2]) return null;
  return decodeSegment(match[2]);
}

export function isArtifactTab(value: string | undefined | null): value is ArtifactTab {
  return Boolean(value && ARTIFACT_TABS.includes(value as ArtifactTab));
}

export function isArtifactsSubTab(value: string | undefined | null): value is ArtifactsSubTab {
  return Boolean(value && ARTIFACTS_SUB_TABS.includes(value as ArtifactsSubTab));
}

export function isStudioSection(value: string | undefined | null): value is StudioSection {
  return Boolean(value && STUDIO_SECTIONS.includes(value as StudioSection));
}

/** The link to a ticket tab, under the ticket's shareable id when it is known.
 *
 * Most callers hold only a UUID (a run row, a queue entry). If this tab has
 * already learned that ticket's shareable id the link uses it; otherwise the
 * UUID link opens the same page and `TicketRouteResolver` swaps the address for
 * the readable one once it has the ticket.
 */
export function ticketPath(ticketId: string, tab: ArtifactTab = "diff"): string {
  const ref = useTicketRefStore.getState().refByUuid[ticketId] ?? ticketId;
  return `/tickets/${encodeURIComponent(ref)}/${tab}`;
}

/** The ticket UUID a ticket path stands for, or null if it is not known yet.
 *
 * The address bar holds a ticket's shareable id (`/tickets/lor-mcp-gateway-142`)
 * or, for a moment after following a UUID link, the UUID itself. Every
 * ticket-scoped endpoint takes the UUID, so a shareable id is looked up in
 * `uuidByRef` — what `TicketRouteResolver` has learned. Before it has, null is
 * the honest answer: every caller already handles "no ticket here", and
 * answering with the ref would fetch 404s under an id no endpoint accepts.
 */
export function ticketIdFromPath(
  pathname: string,
  uuidByRef: Readonly<Record<string, string>>,
): string | null {
  const match = pathname.match(TICKET_PATH_RE);
  if (!match) return null;
  return ticketUuidForRef(decodeSegment(match[1]), uuidByRef);
}

/** A ticket route param as the UUID it names, or null if not yet known. */
export function ticketUuidForRef(
  ref: string,
  uuidByRef: Readonly<Record<string, string>>,
): string | null {
  if (looksLikeTicketUuid(ref)) return ref;
  return uuidByRef[ref] ?? null;
}

export function artifactTabFromPath(pathname: string): ArtifactTab | null {
  const match = pathname.match(TICKET_PATH_RE);
  if (!match?.[2]) return null;
  const tab = decodeSegment(match[2]);
  return isArtifactTab(tab) ? tab : null;
}

export function studioPath(section: StudioSection = "agents"): string {
  return `/studio/${section}`;
}

export function studioSectionFromPath(pathname: string): StudioSection {
  if (pathname === "/studio/workflows" || pathname.startsWith("/studio/workflows/")) {
    return "workflows";
  }
  if (pathname === "/studio/tickets" || pathname.startsWith("/studio/tickets/")) {
    return "tickets";
  }
  if (pathname === "/studio/gates" || pathname.startsWith("/studio/gates/")) {
    return "gates";
  }
  return "agents";
}

const INITIATIVE_PATH_RE = /^\/initiatives\/([^/]+)/;

/** Suggested initiatives drawn from open work. A static segment, so it wins over `:initiativeId`. */
export const INITIATIVE_SUGGESTIONS_PATH = "/initiatives/suggest";

/** An initiative's planning page. */
export function initiativePath(initiativeId: string): string {
  return `/initiatives/${encodeURIComponent(initiativeId)}`;
}

/** The initiative a `/initiatives/:id` URL names, or null on the list, the suggestions and elsewhere. */
export function initiativeIdFromPath(pathname: string): string | null {
  if (pathname === INITIATIVE_SUGGESTIONS_PATH) return null;
  const match = pathname.match(INITIATIVE_PATH_RE);
  return match ? decodeSegment(match[1]) : null;
}

export function viewPath(viewId: string): string {
  return `/view/${encodeURIComponent(viewId)}`;
}

/**
 * The view a route is showing, or null on every other route.
 *
 * `pageFromPath` answers `home` for anything it does not recognise, so a view
 * route would otherwise light up the Home tab. Callers highlighting a tab ask
 * this first.
 */
export function viewIdFromPath(pathname: string): string | null {
  const match = pathname.match(VIEW_PATH_RE);
  return match ? decodeSegment(match[1]) : null;
}

/** The Memory page's tabs, in the order they are drawn. The map is the default. */
export const MEMORY_TABS = ["map", "records", "health"] as const;
export type MemoryTab = (typeof MEMORY_TABS)[number];

const MEMORY_TAB_PATH_RE = /^\/memory\/(records|health)(?:\/|$)/;
const MEMORY_NODE_PATH_RE = /^\/memory\/map\/([^/]+)/;
/** The browser's old home, kept so links and bookmarks into it still land. */
const LEGACY_KNOWLEDGE_PATH_RE = /^\/knowledge(?:\/([^/]+))?\/?$/;

/** A Memory tab's URL; on the map, optionally with one record selected. */
export function memoryPath(tab: MemoryTab, nodeId?: string): string {
  if (tab !== "map") return `/memory/${tab}`;
  return nodeId ? `/memory/map/${encodeURIComponent(nodeId)}` : "/memory";
}

export function memoryTabFromPath(pathname: string): MemoryTab {
  const match = pathname.match(MEMORY_TAB_PATH_RE);
  return match ? (match[1] as MemoryTab) : "map";
}

/**
 * The record a `/memory/map/:nodeId` URL names, or null. Decoded through
 * `decodeSegment`, so a stray `%` reads as itself rather than throwing during
 * render.
 */
export function memoryNodeIdFromPath(pathname: string): string | null {
  const match = pathname.match(MEMORY_NODE_PATH_RE);
  return match ? decodeSegment(match[1]) : null;
}

/** Where an old `/knowledge[/:nodeId]` URL now lives. */
export function memoryPathForLegacyKnowledge(pathname: string): string {
  const match = pathname.match(LEGACY_KNOWLEDGE_PATH_RE);
  return memoryPath("map", match?.[1] ? decodeSegment(match[1]) : undefined);
}

export const WORKSPACES_TABS = ["workspaces", "instances"] as const;
export type WorkspacesTab = (typeof WORKSPACES_TABS)[number];

const WORKSPACES_TAB_PATH_RE = /^\/workspaces\/instances(?:\/|$)/;

/** A Workspaces page tab's URL. The workspace list is the page's own root. */
export function workspacesPath(tab: WorkspacesTab): string {
  return tab === "instances" ? "/workspaces/instances" : "/workspaces";
}

export function workspacesTabFromPath(pathname: string): WorkspacesTab {
  return WORKSPACES_TAB_PATH_RE.test(pathname) ? "instances" : "workspaces";
}

export function pageFromPath(pathname: string): AppPage {
  if (pathname === "/" || pathname === "") return "home";
  if (pathname === "/chat" || pathname.startsWith("/chat/")) return "chat";
  if (pathname === "/console" || pathname.startsWith("/console/")) return "dashboard";
  if (pathname === "/initiatives" || pathname.startsWith("/initiatives/")) return "initiatives";
  if (pathname === "/studio" || pathname.startsWith("/studio/")) return "studio";
  if (pathname === "/editor" || pathname.startsWith("/editor/")) return "editor";
  if (pathname === "/queue" || pathname.startsWith("/queue/")) return "queue";
  if (pathname === "/branch-triage" || pathname.startsWith("/branch-triage/")) {
    return "branch-triage";
  }
  if (pathname === "/mcp" || pathname.startsWith("/mcp/")) return "mcp";
  if (pathname === "/memory" || pathname.startsWith("/memory/")) return "memory";
  if (pathname === "/knowledge" || pathname.startsWith("/knowledge/")) return "memory";
  if (pathname === "/workspaces" || pathname.startsWith("/workspaces/")) return "workspaces";
  if (pathname === "/instances" || pathname.startsWith("/instances/")) return "workspaces";
  // Ticket deep-links still live in the Console shell.
  if (pathname.startsWith("/tickets/")) return "dashboard";
  return "home";
}

export function pathForPage(page: AppPage): string {
  return PAGE_PATHS[page];
}
