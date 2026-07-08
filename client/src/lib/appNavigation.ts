export type AppPage = "dashboard" | "studio" | "editor" | "queue" | "branch-triage";

export type ArtifactTab =
  | "diff"
  | "logs"
  | "tests"
  | "hive"
  | "context"
  | "errors"
  | "triage"
  | "pr";

export type StudioSection = "agents" | "workflows" | "tickets";

export const ARTIFACT_TABS: ArtifactTab[] = [
  "diff",
  "errors",
  "triage",
  "logs",
  "tests",
  "hive",
  "context",
  "pr",
];

export const STUDIO_SECTIONS: StudioSection[] = ["agents", "workflows", "tickets"];

export const STUDIO_NEW_RESOURCE = "new";

const PAGE_PATHS: Record<AppPage, string> = {
  dashboard: "/",
  studio: "/studio/agents",
  editor: "/editor",
  queue: "/queue",
  "branch-triage": "/branch-triage",
};

const TICKET_PATH_RE = /^\/tickets\/([^/]+)(?:\/([^/]+))?/;
const STUDIO_RESOURCE_PATH_RE = /^\/studio\/(agents|workflows|tickets)(?:\/([^/]+))?/;

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
  return decodeURIComponent(match[2]);
}

export function isArtifactTab(value: string | undefined | null): value is ArtifactTab {
  return Boolean(value && ARTIFACT_TABS.includes(value as ArtifactTab));
}

export function isStudioSection(value: string | undefined | null): value is StudioSection {
  return Boolean(value && STUDIO_SECTIONS.includes(value as StudioSection));
}

export function ticketPath(ticketId: string, tab: ArtifactTab = "diff"): string {
  const encodedId = encodeURIComponent(ticketId);
  return `/tickets/${encodedId}/${tab}`;
}

export function ticketIdFromPath(pathname: string): string | null {
  const match = pathname.match(TICKET_PATH_RE);
  return match ? decodeURIComponent(match[1]) : null;
}

export function artifactTabFromPath(pathname: string): ArtifactTab | null {
  const match = pathname.match(TICKET_PATH_RE);
  if (!match?.[2]) return null;
  const tab = decodeURIComponent(match[2]);
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
  return "agents";
}

export function pageFromPath(pathname: string): AppPage {
  if (pathname === "/studio" || pathname.startsWith("/studio/")) return "studio";
  if (pathname === "/editor" || pathname.startsWith("/editor/")) return "editor";
  if (pathname === "/queue" || pathname.startsWith("/queue/")) return "queue";
  if (pathname === "/branch-triage" || pathname.startsWith("/branch-triage/")) {
    return "branch-triage";
  }
  return "dashboard";
}

export function pathForPage(page: AppPage): string {
  return PAGE_PATHS[page];
}
