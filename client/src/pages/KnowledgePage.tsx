import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { KnowledgeGraph, NodeType } from "../api/memoryApi";
import { NODE_TYPES } from "../api/memoryApi";
import { KnowledgeGraphView } from "../components/knowledge/KnowledgeGraphView";
import { KnowledgeList } from "../components/knowledge/KnowledgeList";
import { KnowledgePanel } from "../components/knowledge/KnowledgePanel";
import { PageTopbar } from "../components/TopbarPageSlot";
import { PaneSkeleton } from "../components/ui/PaneSkeleton";
import { knowledgeNodeIdFromPath, knowledgeNodePath } from "../lib/appNavigation";
import { NODE_TYPE_LABELS } from "../lib/knowledgeLayout";
import { formatLocalTimestamp } from "../lib/timestamps";
import { describeError, pushToast } from "../state/toastStore";
import "./KnowledgePage.css";

type View = "graph" | "list";
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
        {(["graph", "list"] as const).map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={view === option}
            className={view === option ? "selected" : ""}
            onClick={() => onView(option)}
          >
            {option === "graph" ? "Graph" : "List"}
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
  return view === "graph" ? (
    <KnowledgeGraphView
      nodes={graph.nodes}
      relations={graph.relations}
      selectedId={selectedId}
      onSelect={onSelect}
    />
  ) : (
    <KnowledgeList nodes={graph.nodes} selectedId={selectedId} onSelect={onSelect} />
  );
}

function WorkspaceKnowledge({ slug }: { slug: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const selectedId = knowledgeNodeIdFromPath(location.pathname);
  const [view, setView] = useState<View>("graph");
  const [draft, setDraft] = useState("");
  const query = useSettled(draft.trim());
  const [nodeType, setNodeType] = useState<NodeType | null>(null);
  // Off by default, as on every read path: discredited records are withdrawn
  // from agents, and the browser shows them only when asked.
  const [includeDiscredited, setIncludeDiscredited] = useState(false);

  const graph = useQuery({
    queryKey: ["memory-graph", slug, query, nodeType, includeDiscredited],
    queryFn: () => api.memoryGraph(slug, { query, nodeType, includeDiscredited }),
    meta: { errorTitle: "Load knowledge graph" },
  });

  // The global query toast stays quiet while earlier data is on screen, so a
  // refresh that fails reports itself rather than looking like "nothing new".
  const refresh = async () => {
    const result = await graph.refetch();
    if (result.error && result.data !== undefined) {
      pushToast({
        tone: "error",
        title: "Refresh knowledge graph failed",
        message: describeError(result.error, "The request failed"),
      });
    }
  };

  const onSelect = (nodeId: string) => navigate(knowledgeNodePath(nodeId));
  const clearFilter = () => {
    setDraft("");
    setNodeType(null);
  };

  let main;
  if (graph.isLoading) {
    main =
      view === "graph" ? (
        <div className="kb-canvas">
          <PaneSkeleton variant="block" label="Loading the graph…" />
        </div>
      ) : (
        <PaneSkeleton variant="list" rows={8} label="Loading records…" />
      );
  } else if (!graph.data) {
    main = (
      <div className="kb-state" role="alert">
        <h2>Could not load the knowledge graph</h2>
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
      <PageTopbar title="Knowledge">
        <span className="kb-counts" aria-live="polite">
          {graph.data ? (
            `${graph.data.counts.entities} records · ${graph.data.counts.links} links`
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
      <div className={`kb-body-grid${selectedId ? " kb-body-grid--panel" : ""}`}>
        <div className="kb-main">{main}</div>
        {selectedId && (
          <KnowledgePanel
            key={selectedId}
            nodeId={selectedId}
            workspaceSlug={slug}
            onSelect={onSelect}
            onClose={() => navigate("/knowledge")}
          />
        )}
      </div>
    </>
  );
}

/**
 * The knowledge browser (766): a workspace's memory graph, as a graph or as a
 * list, with one record's provenance beside it. URL-addressable by record id.
 */
export function KnowledgePage() {
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
    meta: { errorTitle: "Load workspaces" },
  });
  const [chosen, setChosen] = useState<string | null>(null);
  const slug = chosen ?? workspaces.data?.[0]?.slug ?? null;

  let body;
  if (workspaces.isLoading) {
    body = <PaneSkeleton variant="list" rows={6} label="Loading workspaces…" />;
  } else if (!workspaces.data) {
    body = (
      <div className="kb-state" role="alert">
        <p>Could not load workspaces: {describeError(workspaces.error, "the request failed")}.</p>
        <button type="button" className="btn-secondary" onClick={() => void workspaces.refetch()}>
          Try again
        </button>
      </div>
    );
  } else if (!slug) {
    body = <p className="kb-state">No workspaces yet. Add one to start recording memory.</p>;
  } else {
    body = (
      <>
        <label className="kb-workspace">
          <span>Workspace</span>
          <select value={slug} onChange={(event) => setChosen(event.target.value)}>
            {workspaces.data.map((ws) => (
              <option key={ws.id} value={ws.slug}>
                {ws.name}
              </option>
            ))}
          </select>
        </label>
        <WorkspaceKnowledge key={slug} slug={slug} />
      </>
    );
  }
  return <div className="screen-view screen-view--knowledge">{body}</div>;
}
