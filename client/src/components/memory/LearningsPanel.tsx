/**
 * The learnings in one workspace's memory graph, discredited ones included on
 * request — the rows the discredit control acts on are exactly the ones every
 * agent read path hides, so this list has to be able to show them.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { MemoryNode } from "../../api/memoryApi";
import { describeError } from "../../state/toastStore";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { LearningDetail } from "./LearningDetail";

function NodeRow({
  node,
  selected,
  onSelect,
}: {
  node: MemoryNode;
  selected: boolean;
  onSelect: () => void;
}) {
  const observed = node.confidence.observations;
  return (
    <li>
      <button
        type="button"
        className={`memory-node-row${selected ? " selected" : ""}${node.discredited ? " discredited" : ""}`}
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span className="memory-node-title">{node.title}</span>
        <span className="memory-node-meta">
          {node.discredited
            ? "discredited"
            : observed === 0
              ? "not observed"
              : `${Math.round(node.confidence.mean * 100)}% · ${observed} runs`}
        </span>
      </button>
    </li>
  );
}

function NodeList({
  workspaceSlug,
  includeDiscredited,
}: {
  workspaceSlug: string;
  includeDiscredited: boolean;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const nodes = useQuery({
    queryKey: ["memory-nodes", workspaceSlug, includeDiscredited],
    queryFn: () => api.memoryNodes(workspaceSlug, includeDiscredited),
    meta: { errorTitle: "Load learnings" },
  });

  if (nodes.isLoading) return <PaneSkeleton variant="list" rows={6} label="Loading learnings…" />;
  if (!nodes.data) {
    return (
      <div className="memory-error" role="alert">
        <p>Could not load learnings: {describeError(nodes.error, "the request failed")}.</p>
        <button type="button" className="btn-secondary" onClick={() => void nodes.refetch()}>
          Try again
        </button>
      </div>
    );
  }
  const list = nodes.data.nodes;
  if (list.length === 0) {
    return (
      <p className="memory-empty">
        No learnings in {workspaceSlug}
        {includeDiscredited ? "" : " (discredited ones are hidden)"}. Agents record them with
        loregarden_append_learning as they finish work.
      </p>
    );
  }
  const selected = list.find((node) => node.id === selectedId) ?? null;
  return (
    <div className="memory-learnings-body">
      <ul className="memory-node-list" aria-label="Learnings">
        {list.map((node) => (
          <NodeRow
            key={node.id}
            node={node}
            selected={node.id === selectedId}
            onSelect={() => setSelectedId(node.id)}
          />
        ))}
      </ul>
      {selected ? (
        <LearningDetail key={selected.id} node={selected} workspaceSlug={workspaceSlug} />
      ) : (
        <p className="memory-muted memory-detail-placeholder">
          Select a learning to see its confidence and history.
        </p>
      )}
    </div>
  );
}

export function LearningsPanel() {
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
    meta: { errorTitle: "Load workspaces" },
  });
  const [chosen, setChosen] = useState<string | null>(null);
  const [includeDiscredited, setIncludeDiscredited] = useState(true);
  const slug = chosen ?? workspaces.data?.[0]?.slug ?? null;

  let body;
  if (workspaces.isLoading) {
    body = <PaneSkeleton variant="list" rows={4} label="Loading workspaces…" />;
  } else if (!workspaces.data) {
    body = (
      <p className="memory-error" role="alert">
        Could not load workspaces: {describeError(workspaces.error, "the request failed")}.
      </p>
    );
  } else if (!slug) {
    body = <p className="memory-empty">No workspaces yet. Add one to start recording memory.</p>;
  } else {
    body = (
      <NodeList
        key={`${slug}:${includeDiscredited}`}
        workspaceSlug={slug}
        includeDiscredited={includeDiscredited}
      />
    );
  }

  return (
    <section className="memory-panel" aria-labelledby="memory-learnings-title">
      <header className="memory-panel-header">
        <h2 id="memory-learnings-title" className="memory-panel-title">
          Learnings
        </h2>
        <div className="memory-filters">
          <label>
            <span>Workspace</span>
            <select
              value={slug ?? ""}
              disabled={!workspaces.data?.length}
              onChange={(event) => setChosen(event.target.value)}
            >
              {(workspaces.data ?? []).map((ws) => (
                <option key={ws.id} value={ws.slug}>
                  {ws.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={includeDiscredited}
              onChange={(event) => setIncludeDiscredited(event.target.checked)}
            />
            <span>Show discredited</span>
          </label>
        </div>
      </header>
      {body}
    </section>
  );
}
