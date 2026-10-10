import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import type { KnowledgeGraph, NodeType } from "../../api/memoryApi";
import { NODE_TYPES } from "../../api/memoryApi";
import { memoryNodeIdFromPath, memoryPath } from "../../lib/appNavigation";
import { NODE_TYPE_LABELS } from "../../lib/knowledgeLayout";
import { inferredEdges } from "../../lib/memoryInferred";
import { formatLocalTimestamp } from "../../lib/timestamps";
import { describeError, pushToast } from "../../state/toastStore";
import { PageTopbar } from "../TopbarPageSlot";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { KnowledgeList } from "./KnowledgeList";
import { KnowledgePanel } from "./KnowledgePanel";
import { MapOverview } from "./MapOverview";
import { MemoryMap } from "./MemoryMap";
import "./MemoryMapTab.css";

type View = "map" | "list";
const FILTER_DEBOUNCE_MS = 300;

/** The typed filter, settled: the graph is fetched per settled query, not per keystroke. */
function useSettled(value: string): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [value]);
  return settled;
}

function Toolbar({
  graph,
  view,
  onView,
  draft,
  onDraft,
  nodeType,
  onNodeType,
  includeDiscredited,
  onIncludeDiscredited,
}: {
  graph: KnowledgeGraph | undefined;
  view: View;
  onView: (view: View) => void;
  draft: string;
  onDraft: (value: string) => void;
  nodeType: NodeType | null;
  onNodeType: (type: NodeType | null) => void;
  includeDiscredited: boolean;
  onIncludeDiscredited: (value: boolean) => void;
}) {
  const present = NODE_TYPES.filter((type) => (graph?.type_counts[type] ?? 0) > 0);
  return (
    <div className="kb-toolbar">
      <div className="kb-toggle" role="radiogroup" aria-label="View">
        {(["map", "list"] as const).map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={view === option}
            className={view === option ? "selected" : ""}
            onClick={() => onView(option)}
          >
            {option === "map" ? "Graph" : "List"}
          </button>
        ))}
      </div>
      <label className="kb-filter">
        <span>Find in records</span>
        <input type="search" value={draft} onChange={(event) => onDraft(event.target.value)} />
      </label>
      <label className="kb-filter">
        <input
          type="checkbox"
          checked={includeDiscredited}
          onChange={(event) => onIncludeDiscredited(event.target.checked)}
        />
        <span>Show discredited</span>
      </label>
      {present.length > 0 && (
        <div className="kb-chips" aria-label="Record types">
          <button
            type="button"
            aria-pressed={nodeType === null}
            className={nodeType === null ? "selected" : ""}
            onClick={() => onNodeType(null)}
          >
            All
          </button>
          {present.map((type) => (
            <button
              key={type}
              type="button"
              aria-pressed={nodeType === type}
              className={nodeType === type ? "selected" : ""}
              onClick={() => onNodeType(nodeType === type ? null : type)}
            >
              {NODE_TYPE_LABELS[type]} · {graph?.type_counts[type]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Records({
  graph,
  view,
  selectedId,
  onSelect,
  onClearFilter,
}: {
  graph: KnowledgeGraph;
  view: View;
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
  onClearFilter: () => void;
}) {
  // Memoised: a fresh array each render would re-run the map's force layout.
  const inferred = useMemo(() => inferredEdges(graph.inferred), [graph.inferred]);
  if (!graph.configured) {
    return (
      <div className="kb-state" role="status">
        <h2>No memory graph is configured</h2>
        <p className="kb-muted">
          Set a memory SQLite location under Settings → Memory in the sidebar. Agents record
          learnings into it once it exists.
        </p>
      </div>
    );
  }
  if (graph.nodes.length === 0) {
    const filtered = graph.query || graph.node_type;
    if (filtered) {
      const what = [
        graph.query ? `“${graph.query}”` : null,
        graph.node_type ? NODE_TYPE_LABELS[graph.node_type] : null,
      ]
        .filter(Boolean)
        .join(" in ");
      return (
        <div className="kb-state" role="status">
          <h2>No records match {what}</h2>
          <button type="button" className="btn-secondary" onClick={onClearFilter}>
            Clear filter
          </button>
        </div>
      );
    }
    return (
      <div className="kb-state" role="status">
        <h2>No memories recorded yet</h2>
        <p className="kb-muted">
          Agents write learnings when they complete a stage (loregarden_append_learning). They
          appear here as soon as one is recorded.
        </p>
      </div>
    );
  }
  return view === "map" ? (
    <MemoryMap
      nodes={graph.nodes}
      relations={graph.relations}
      inferred={inferred}
      selectedId={selectedId}
      onSelect={onSelect}
    />
  ) : (
    <KnowledgeList nodes={graph.nodes} selectedId={selectedId} onSelect={onSelect} />
  );
}

/**
 * The Memory page's map tab: one workspace's records as a map or a list, the
 * map's overview beside it, and one record's panel in its place when a record
 * is selected. URL-addressable by record id (`/memory/map/:nodeId`).
 */
export function MemoryMapTab({ slug }: { slug: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const selectedId = memoryNodeIdFromPath(location.pathname);
  const [view, setView] = useState<View>("map");
  const [draft, setDraft] = useState("");
  const query = useSettled(draft.trim());
  const [nodeType, setNodeType] = useState<NodeType | null>(null);
  // Off by default, as on every read path: discredited records are withdrawn
  // from agents, and the browser shows them only when asked.
  const [includeDiscredited, setIncludeDiscredited] = useState(false);

  const graph = useQuery({
    queryKey: ["memory-graph", slug, query, nodeType, includeDiscredited],
    queryFn: () => api.memoryGraph(slug, { query, nodeType, includeDiscredited }),
    meta: { errorTitle: "Load memory map" },
  });

  // The global query toast stays quiet while earlier data is on screen, so a
  // refresh that fails reports itself rather than looking like "nothing new".
  const refresh = async () => {
    const result = await graph.refetch();
    if (result.error && result.data !== undefined) {
      pushToast({
        tone: "error",
        title: "Refresh memory map failed",
        message: describeError(result.error, "The request failed"),
      });
    }
  };

  const onSelect = (nodeId: string) => navigate(memoryPath("map", nodeId));
  const clearFilter = () => {
    setDraft("");
    setNodeType(null);
  };

  const records = graph.data?.nodes ?? [];
  let side = null;
  if (selectedId) {
    side = (
      <KnowledgePanel
        key={selectedId}
        nodeId={selectedId}
        workspaceSlug={slug}
        groups={graph.data?.inferred ?? []}
        nodes={records}
        onSelect={onSelect}
        onClose={() => navigate(memoryPath("map"))}
      />
    );
  } else if (graph.data && records.length > 0) {
    side = (
      <MapOverview
        nodes={records}
        relations={graph.data.relations}
        inferred={graph.data.inferred}
        truncated={graph.data.truncated}
        onSelect={onSelect}
      />
    );
  }

  let main;
  if (graph.isLoading) {
    main =
      view === "map" ? (
        <div className="mm-canvas">
          <PaneSkeleton variant="block" label="Loading the map…" />
        </div>
      ) : (
        <PaneSkeleton variant="list" rows={8} label="Loading records…" />
      );
  } else if (!graph.data) {
    main = (
      <div className="kb-state" role="alert">
        <h2>Could not load the memory map</h2>
        <p>{describeError(graph.error, "The request failed")}</p>
        <button type="button" className="btn-secondary" onClick={() => void refresh()}>
          Try again
        </button>
      </div>
    );
  } else {
    main = (
      <Records
        graph={graph.data}
        view={view}
        selectedId={selectedId}
        onSelect={onSelect}
        onClearFilter={clearFilter}
      />
    );
  }

  return (
    <>
      <PageTopbar title="Memory">
        <span className="kb-counts" aria-live="polite">
          {graph.data ? (
            `${graph.data.counts.entities} records · ${graph.data.counts.links} recorded links · ${graph.data.inferred.length} groups`
          ) : (
            <span className="kb-count-skeleton" aria-label="Loading counts" />
          )}
        </span>
        <span className="kb-checked">
          Last checked {graph.data ? formatLocalTimestamp(graph.data.checked_at) : "—"}
        </span>
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={graph.isFetching}
          aria-busy={graph.isFetching}
          onClick={() => void refresh()}
        >
          {graph.isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </PageTopbar>
      <Toolbar
        graph={graph.data}
        view={view}
        onView={setView}
        draft={draft}
        onDraft={setDraft}
        nodeType={nodeType}
        onNodeType={setNodeType}
        includeDiscredited={includeDiscredited}
        onIncludeDiscredited={setIncludeDiscredited}
      />
      <div className={`kb-body-grid${side ? " kb-body-grid--panel" : ""}`}>
        <div className="kb-main">{main}</div>
        {side}
      </div>
    </>
  );
}
