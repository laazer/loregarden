/**
 * The built-in pages the sidebar can pin, with the icons the fixed rail drew.
 *
 * `page_key` is opaque to the server — it stores whatever it is sent — so this
 * catalog is the only place a stored key becomes a route. A key with no entry
 * here is an unknown page: it keeps its place in the ranking and is not drawn,
 * rather than being routed on blind.
 */

import type { ReactNode } from "react";

import { pathForPage, type AppPage } from "../lib/appNavigation";

export interface SidebarPageDef {
  key: AppPage;
  label: string;
  path: string;
  icon: ReactNode;
  /** Pages that own more of the app than their own route — Console holds ticket deep links. */
  ownsPage: (page: AppPage) => boolean;
}

const STROKE = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.9,
} as const;

function ownsOnly(key: AppPage): (page: AppPage) => boolean {
  return (page) => page === key;
}

/**
 * Declaration order is the seed order: a workspace with no pins gets exactly
 * this sequence, which is the order the fixed rail drew.
 */
export const SIDEBAR_PAGES: SidebarPageDef[] = [
  {
    key: "home",
    label: "Home",
    path: pathForPage("home"),
    ownsPage: ownsOnly("home"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <path d="M3 11.5 12 4l9 7.5" />
        <path d="M6 10.5V20h12v-9.5" />
      </svg>
    ),
  },
  {
    key: "chat",
    label: "Chat",
    path: pathForPage("chat"),
    ownsPage: ownsOnly("chat"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7A2.5 2.5 0 0 1 17.5 16H10l-4 3.2V16H6.5A2.5 2.5 0 0 1 4 13.5z" />
      </svg>
    ),
  },
  {
    key: "dashboard",
    label: "Console",
    path: pathForPage("dashboard"),
    // The editor and ticket deep links live in the Console shell.
    ownsPage: (page) => page === "dashboard" || page === "editor",
    icon: (
      <svg {...STROKE} aria-hidden>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M3 9h18M7 13l2 2-2 2M12 17h4" />
      </svg>
    ),
  },
  {
    key: "studio",
    label: "Studios",
    path: pathForPage("studio"),
    ownsPage: ownsOnly("studio"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <path d="M12 3 3 8v8l9 5 9-5V8z" />
        <path d="M3 8l9 5 9-5M12 13v8" />
      </svg>
    ),
  },
  {
    key: "queue",
    label: "Parallel Execution",
    path: pathForPage("queue"),
    ownsPage: ownsOnly("queue"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <rect x="3" y="4" width="5" height="16" rx="1.5" />
        <rect x="10" y="4" width="5" height="16" rx="1.5" />
        <rect x="17" y="4" width="4" height="16" rx="1.5" />
      </svg>
    ),
  },
  {
    key: "mcp",
    label: "MCP Gateway",
    path: pathForPage("mcp"),
    ownsPage: ownsOnly("mcp"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <circle cx="12" cy="12" r="2.5" />
        <path d="M12 3v6.5M12 14.5V21M3 12h6.5M14.5 12H21" />
        <circle cx="5" cy="5" r="1.6" />
        <circle cx="19" cy="19" r="1.6" />
      </svg>
    ),
  },
  {
    key: "branch-triage",
    label: "Branch Triage",
    path: pathForPage("branch-triage"),
    ownsPage: ownsOnly("branch-triage"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <circle cx="6" cy="6" r="3" />
        <circle cx="6" cy="18" r="3" />
        <path d="M6 9v6" />
        <circle cx="18" cy="6" r="3" />
        <path d="M18 9a9 9 0 0 1-9 9" />
      </svg>
    ),
  },
];

/** The seed a workspace with no stored pins gets, in the fixed rail's order. */
export const DEFAULT_PINNED_PAGE_KEYS: string[] = SIDEBAR_PAGES.map((page) => page.key);

const PAGES_BY_KEY = new Map(SIDEBAR_PAGES.map((page) => [page.key as string, page]));

export function sidebarPageForKey(pageKey: string): SidebarPageDef | undefined {
  return PAGES_BY_KEY.get(pageKey);
}
