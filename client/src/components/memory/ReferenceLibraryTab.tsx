import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../../api/client";
import { memoryLibrarySourceFromPath, memoryPath } from "../../lib/appNavigation";
import {
  DEFAULT_SORT,
  LIBRARY_VIEWS,
  defaultView,
  filterRows,
  nextSort,
  parseView,
  sortRows,
  viewCounts,
  type LibrarySort,
  type LibrarySortKey,
  type LibraryView,
} from "../../lib/referenceLibrary";
import { describeError, pushToast } from "../../state/toastStore";
import { PageTopbar } from "../TopbarPageSlot";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { ReferenceLibraryTable } from "./ReferenceLibraryTable";
import { ReferenceSourceDetail } from "./ReferenceSourceDetail";
import "./ReferenceLibrary.css";

const EMPTY_VIEW_COPY: Record<LibraryView, string> = {
  queue: "Nothing is waiting to be surveyed.",
  surveyed: "No source has been surveyed for this workspace yet.",
  refused: "No source has been refused.",
  all: "",
};

/**
 * The reference library: every repository and site the survey has queued,
 * read or refused for this workspace, and what each one changed — the tickets
 * it touched and the learnings recorded on them. The list answers "which one do
 * I open?" and compares sources; the open source is in the URL, so it can be
 * linked to.
 */
export function ReferenceLibraryTab({ slug }: { slug: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = memoryLibrarySourceFromPath(location.pathname);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<LibrarySort>(DEFAULT_SORT);

  const library = useQuery({
    queryKey: ["reference-library", slug],
    queryFn: () => api.referenceLibrary(slug),
    refetchInterval: 60_000,
  });

  useEffect(() => {
    if (library.isError) {
      pushToast({
        tone: "error",
        title: "Reference library did not load",
        message: describeError(library.error, "the reference library request failed"),
      });
    }
  }, [library.isError, library.error]);

  const view: LibraryView =
    parseView(searchParams.get("view")) ?? (library.data ? defaultView(library.data) : "queue");
  const counts = library.data ? viewCounts(library.data) : null;
  const rows = useMemo(
    () => (library.data ? sortRows(filterRows(library.data.sources, view, search), sort) : []),
    [library.data, view, search, sort],
  );
  const searching = search.trim() !== "";

  function setView(next: LibraryView) {
    const params = new URLSearchParams(searchParams);
    params.set("view", next);
    setSearchParams(params, { replace: true });
  }

  function close() {
    navigate({ pathname: memoryPath("library"), search: location.search });
  }

  let list;
  if (library.isPending) {
    list = <PaneSkeleton variant="list" rows={10} label="Loading the reference library…" />;
  } else if (library.isError && !library.data) {
    list = (
      <div className="rl-state" role="alert">
        <p>The reference library did not load: {describeError(library.error, "the request failed")}.</p>
        <Button variant="secondary" compact disabled={library.isFetching} onClick={() => void library.refetch()}>
          {library.isFetching ? "Retrying…" : "Retry"}
        </Button>
      </div>
    );
  } else if (library.data && library.data.sources.length === 0) {
    list = (
      <div className="rl-state">
        <p>
          No repositories or sites are in the reference library for this workspace yet. A survey
          source is proposed with <code>loregarden_survey_queue</code> (action <code>enqueue</code>)
          and appears here, waiting for your approval in the inbox. The old scratch record is
          brought in once with <code>loregarden survey import-legacy</code>.
        </p>
      </div>
    );
  } else if (rows.length === 0) {
    list = (
      <div className="rl-state">
        <p>{searching ? `No source in ${view === "all" ? "the library" : `“${view}”`} matches “${search.trim()}”.` : EMPTY_VIEW_COPY[view]}</p>
        {searching ? (
          <Button variant="secondary" compact onClick={() => setSearch("")}>
            Clear search
          </Button>
        ) : view !== "all" ? (
          <Button variant="secondary" compact onClick={() => setView("all")}>
            Show all {counts?.all ?? 0} sources
          </Button>
        ) : null}
      </div>
    );
  } else {
    list = (
      <ReferenceLibraryTable
        rows={rows}
        sort={sort}
        onSort={(key: LibrarySortKey) => setSort((current) => nextSort(current, key))}
        selectedId={selectedId}
        search={location.search}
        workspace={slug}
      />
    );
  }

  return (
    <div className={`rl-page${selectedId ? " rl-page--open" : ""}`}>
      <PageTopbar title="Memory">
        <span className="topbar-page-note">
          Repositories and sites surveyed for patterns, and what each changed
        </span>
      </PageTopbar>
      <div className="rl-body">
        <section className="rl-list" aria-label="Sources">
          <div className="rl-toolbar">
            <div className="rl-views" role="group" aria-label="Which sources">
              {LIBRARY_VIEWS.map(({ key, label }) => (
                <Button
                  key={key}
                  variant="plain"
                  className={`rl-view${view === key ? " rl-view--active" : ""}`}
                  aria-pressed={view === key}
                  onClick={() => setView(key)}
                >
                  {label}
                  <span className="count-pill">{counts ? counts[key] : "…"}</span>
                </Button>
              ))}
            </div>
            <Input
              type="search"
              className="rl-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search name, URL or licence"
              aria-label="Search sources"
            />
            <span className="rl-count" aria-live="polite">
              {library.data ? `${rows.length} source${rows.length === 1 ? "" : "s"}` : ""}
              {library.isFetching && library.data ? " · refreshing" : ""}
            </span>
          </div>
          {list}
        </section>
        {selectedId ? (
          <ReferenceSourceDetail key={selectedId} sourceId={selectedId} onClose={close} />
        ) : null}
      </div>
    </div>
  );
}
