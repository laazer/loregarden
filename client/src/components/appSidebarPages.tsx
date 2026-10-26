/**
 * The application's own pages, with the icons the fixed rail drew.
 *
 * This catalog *is* the sidebar's page groups — they are derived from
 * it and stored nowhere. That is the point: a permanent section held as rows
 * can be emptied by one bad write, and the user is then left with no navigation
 * and no control that brings it back. Keys are typed against `AppPage` and
 * every path comes from `pathForPage`, so the pages cannot drift from the routes
 * the app actually has.
 */

import type { ReactNode } from "react";

import { pathForPage, type AppPage } from "../lib/appNavigation";

/**
 * The rail's three groups, in render order: the pages you work in, the pages
 * you build with, and the pages about the machine itself. Thirteen icons in one
 * column gave no clue which was which (Queue, Machine and Instances looked
 * alike); a heading per group does.
 */
export const SIDEBAR_GROUPS = [
  { key: "work", label: "Work" },
  { key: "build", label: "Build" },
  { key: "system", label: "System" },
] as const;

export type SidebarGroup = (typeof SIDEBAR_GROUPS)[number]["key"];

export interface SidebarPageDef {
  key: AppPage;
  group: SidebarGroup;
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

/** Declaration order is render order within a group, and groups are contiguous. */
export const SIDEBAR_PAGES: SidebarPageDef[] = [
  {
    key: "home",
    group: "work",
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
    group: "work",
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
    group: "work",
    label: "Console",
    path: pathForPage("dashboard"),
    ownsPage: ownsOnly("dashboard"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M3 9h18M7 13l2 2-2 2M12 17h4" />
      </svg>
    ),
  },
  {
    key: "work-items",
    group: "work",
    label: "Work items",
    path: pathForPage("work-items"),
    ownsPage: ownsOnly("work-items"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <path d="M9 6h11M9 12h11M9 18h11" />
        <path d="M4 6h.01M4 12h.01M4 18h.01" />
      </svg>
    ),
  },
  {
    key: "initiatives",
    group: "work",
    label: "Initiatives",
    path: pathForPage("initiatives"),
    ownsPage: ownsOnly("initiatives"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <path d="M5 21V4" />
        <path d="M5 4h11l-2 4 2 4H5" />
      </svg>
    ),
  },
  {
    key: "editor",
    group: "build",
    label: "Editor",
    path: pathForPage("editor"),
    ownsPage: ownsOnly("editor"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11l1.8 2H18.5A1.5 1.5 0 0 1 20 7.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z" />
        <path d="M9 15l2-2-2-2M13.5 16h3" />
      </svg>
    ),
  },
  {
    key: "studio",
    group: "build",
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
    key: "branch-triage",
    group: "build",
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
  {
    key: "machine",
    group: "system",
    label: "Machine",
    path: pathForPage("machine"),
    ownsPage: ownsOnly("machine"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <rect x="3" y="4" width="18" height="12" rx="1.5" />
        <path d="M8 20h8M12 16v4" />
        <path d="M7 12V9M11 12V8M15 12v-2" />
      </svg>
    ),
  },
  {
    key: "queue",
    group: "system",
    label: "Queue",
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
    group: "system",
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
    key: "workspaces",
    group: "system",
    label: "Workspaces",
    path: pathForPage("workspaces"),
    ownsPage: ownsOnly("workspaces"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <rect x="3" y="4" width="18" height="7" rx="1.5" />
        <rect x="3" y="13" width="18" height="7" rx="1.5" />
        <path d="M7 7.5h.01M7 16.5h.01" />
      </svg>
    ),
  },
  {
    key: "memory",
    group: "system",
    label: "Memory",
    path: pathForPage("memory"),
    ownsPage: ownsOnly("memory"),
    icon: (
      <svg {...STROKE} aria-hidden>
        <path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a2 2 0 0 0-3-1z" />
        <path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1" />
      </svg>
    ),
  },
];
