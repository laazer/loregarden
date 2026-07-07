export type AppPage = "dashboard" | "studio" | "editor" | "queue";

const PAGE_PATHS: Record<AppPage, string> = {
  dashboard: "/",
  studio: "/studio",
  editor: "/editor",
  queue: "/queue",
};

const TICKET_PATH_RE = /^\/tickets\/([^/]+)/;

export function ticketPath(ticketId: string): string {
  return `/tickets/${encodeURIComponent(ticketId)}`;
}

export function ticketIdFromPath(pathname: string): string | null {
  const match = pathname.match(TICKET_PATH_RE);
  return match ? decodeURIComponent(match[1]) : null;
}

export function pageFromPath(pathname: string): AppPage {
  if (pathname === "/studio" || pathname.startsWith("/studio/")) return "studio";
  if (pathname === "/editor" || pathname.startsWith("/editor/")) return "editor";
  if (pathname === "/queue" || pathname.startsWith("/queue/")) return "queue";
  return "dashboard";
}

export function pathForPage(page: AppPage): string {
  return PAGE_PATHS[page];
}
