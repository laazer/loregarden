import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { PageTopbar } from "../components/TopbarPageSlot";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { PaneSkeleton } from "../components/ui/PaneSkeleton";
import { Select } from "../components/ui/Select";
import { WorkItemsTable } from "../components/workItems/WorkItemsTable";
import {
  DEFAULT_WORK_ITEMS_SORT,
  WORK_ITEMS_VIEWS,
  flattenWorkItems,
  inView,
  matchesSearch,
  parseWorkItemsView,
  sortWorkItems,
  viewCounts,
  workspaceOptions,
  type WorkItemsSort,
  type WorkItemsSortKey,
  type WorkItemsView,
} from "../lib/workItemsTable";
import { describeError, pushToast } from "../state/toastStore";
import "./WorkItemsPage.css";

/** Rows drawn at once; the rest are a click away, so 1,200 rows never mount together. */
const PAGE_SIZE = 200;

const EMPTY_COPY: Record<WorkItemsView, string> = {
  needs_me:
    "Nothing needs you right now. A ticket lands here when a stage waits for your approval or answer, or is blocked on something only a person can do.",
  in_flight: "No ticket is in progress.",
  blocked: "No ticket is blocked.",
  open: "Every ticket is done or won't do.",
  all: "There are no work items yet. Create one from the Console.",
};

/**
 * Every work item, as a table, answering "which tickets need me now, and why?"
 * by default and "where is everything?" one tab over. The Console's tree is
 * for working one ticket; this page is for finding it among a thousand.
 */
export function WorkItemsPage() {
  const [params, setParams] = useSearchParams();
  const view = parseWorkItemsView(params.get("view"));
  const [search, setSearch] = useState("");
  const [workspace, setWorkspace] = useState("all");
  const [sort, setSort] = useState<WorkItemsSort>(DEFAULT_WORK_ITEMS_SORT);
  // How many rows are drawn, for the filters it was chosen under: any change
  // of view, search, workspace or sort starts again at one page. Derived rather
  // than reset in an effect, which runs after the commit and could undo a
  // "Show more" clicked in between.
  const [paging, setPaging] = useState({ key: "", count: PAGE_SIZE });

  const tree = useQuery({
    queryKey: ["ticket-tree", "work-items"],
    queryFn: () => api.ticketTree({}),
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (tree.isError) {
      pushToast({ tone: "error", title: "Work items did not load", message: describeError(tree.error) });
    }
  }, [tree.isError, tree.error]);

  const all = useMemo(() => flattenWorkItems(tree.data ?? []), [tree.data]);
  const inWorkspace = useMemo(
    () => (workspace === "all" ? all : all.filter((row) => row.node.workspace_slug === workspace)),
    [all, workspace],
  );
  const counts = useMemo(() => viewCounts(inWorkspace), [inWorkspace]);
  const rows = useMemo(
    () => sortWorkItems(inWorkspace.filter((row) => inView(view, row.node) && matchesSearch(row, search)), sort),
    [inWorkspace, view, search, sort],
  );

  const pageKey = `${view}|${search}|${workspace}|${sort.key}|${sort.dir}`;
  const shown = paging.key === pageKey ? paging.count : PAGE_SIZE;

  const setView = (next: WorkItemsView) =>
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      if (next === "needs_me") out.delete("view");
      else out.set("view", next);
      return out;
    });

  const toggleSort = (key: WorkItemsSortKey) =>
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "ascending" ? "descending" : "ascending" }
        : { key, dir: "ascending" },
    );

  const filtering = search.trim() !== "" || workspace !== "all";

  return (
    <div className="screen-view wi-page">
      <PageTopbar title="Work items">
        <span className="topbar-page-note">Every ticket across workspaces; open one in the Console</span>
      </PageTopbar>

      <div className="wi-toolbar">
        <div className="wi-views" role="group" aria-label="Which work items">
          {WORK_ITEMS_VIEWS.map(({ key, label }) => (
            <Button
              key={key}
              variant="plain"
              className={`wi-view${view === key ? " wi-view--active" : ""}`}
              aria-pressed={view === key}
              onClick={() => setView(key)}
            >
              {label}
              <span className="count-pill">{tree.data ? counts[key] : "…"}</span>
            </Button>
          ))}
        </div>
        <Input
          type="search"
          className="wi-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search title, id or parent"
          aria-label="Search work items"
        />
        <Select aria-label="Workspace" value={workspace} onChange={(event) => setWorkspace(event.target.value)}>
          <option value="all">All workspaces ({all.length})</option>
          {workspaceOptions(all).map(([slug, count]) => (
            <option key={slug} value={slug}>
              {slug} ({count})
            </option>
          ))}
        </Select>
        <span className="wi-count" aria-live="polite">
          {tree.data ? `${rows.length} ticket${rows.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>

      <div className="wi-body">
        {tree.isPending ? (
          <PaneSkeleton variant="list" rows={12} label="Loading work items…" />
        ) : tree.isError && !tree.data ? (
          <div className="wi-state" role="alert">
            <p>Work items did not load: {describeError(tree.error, "the ticket tree request failed")}.</p>
            <Button variant="secondary" compact disabled={tree.isFetching} onClick={() => void tree.refetch()}>
              {tree.isFetching ? "Retrying…" : "Retry"}
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <div className="wi-state">
            <p>{filtering ? "No work items match this search." : EMPTY_COPY[view]}</p>
            {filtering ? (
              <Button
                variant="secondary"
                compact
                onClick={() => {
                  setSearch("");
                  setWorkspace("all");
                }}
              >
                Clear search
              </Button>
            ) : view !== "open" && view !== "all" ? (
              <Button variant="secondary" compact onClick={() => setView("open")}>
                Show open tickets ({counts.open})
              </Button>
            ) : null}
          </div>
        ) : (
          <>
            <WorkItemsTable rows={rows.slice(0, shown)} sort={sort} onSort={toggleSort} showWhy={view === "needs_me"} />
            {rows.length > shown ? (
              <div className="wi-more">
                <Button variant="secondary" compact onClick={() => setPaging({ key: pageKey, count: shown + PAGE_SIZE })}>
                  Show {Math.min(PAGE_SIZE, rows.length - shown)} more of {rows.length - shown} remaining
                </Button>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
