export type AppPage = "dashboard" | "studio" | "editor" | "queue";

const PAGE_PATHS: Record<AppPage, string> = {
  dashboard: "/",
  studio: "/studio",
  editor: "/editor",
  queue: "/queue",
};

export function pageFromPath(pathname: string): AppPage {
  if (pathname === "/studio" || pathname.startsWith("/studio/")) return "studio";
  if (pathname === "/editor" || pathname.startsWith("/editor/")) return "editor";
  if (pathname === "/queue" || pathname.startsWith("/queue/")) return "queue";
  return "dashboard";
}

export function pathForPage(page: AppPage): string {
  return PAGE_PATHS[page];
}

export function syncAppPageUrl(page: AppPage, replace = false) {
  if (typeof window === "undefined") return;
  const path = pathForPage(page);
  if (window.location.pathname === path) return;
  const state = { appPage: page };
  if (replace) {
    window.history.replaceState(state, "", path);
  } else {
    window.history.pushState(state, "", path);
  }
}

export function initialAppPage(): AppPage {
  if (typeof window === "undefined") return "dashboard";
  return pageFromPath(window.location.pathname);
}
