/**
 * The memory map: a workspace's records as a force-directed graph.
 *
 * Colour is record type, size is how many recorded links a record has, and a
 * line is a recorded relationship — styled by its type, with contradictions
 * and supersessions dashed so they read without colour. Linked records settle
 * together; position carries nothing else, and the legend says so.
 *
 * Hovering or focusing a record lights its neighbourhood and dims the rest.
 * Every record is a focusable button (Enter or Space selects), and the list
 * view carries the same records for anyone who would rather not use a canvas.
 */

import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { GraphNode, GraphRelation, NodeType, RelationType } from "../../api/memoryApi";
import { RELATION_TYPES } from "../../api/memoryApi";
import { NODE_TYPE_LABELS } from "../../lib/knowledgeLayout";
import { layoutMemoryMap, neighbourhood } from "../../lib/memoryMapLayout";

const TYPE_COLOURS: Record<NodeType, string> = {
  memory: "var(--blue)",
  learning: "var(--ac)",
};

interface EdgeStyle {
  colour: string;
  dash?: string;
  label: string;
}

const RELATION_STYLES: Record<RelationType, EdgeStyle> = {
  related: { colour: "var(--bd2)", label: "Related" },
  supports: { colour: "var(--grn)", label: "Supports" },
  contradicts: { colour: "var(--red)", dash: "6 4", label: "Contradicts" },
  extends: { colour: "var(--vio)", label: "Extends" },
  part_of: { colour: "var(--bll)", label: "Part of" },
  applies: { colour: "var(--aml)", label: "Applies" },
  supersedes: { colour: "var(--amb)", dash: "2 4", label: "Supersedes" },
};

const KNOWN_RELATIONS: ReadonlySet<string> = new Set(RELATION_TYPES);

/** A relation written before the vocabulary closed draws, and filters, as a plain link. */
function relationKey(relation: string): RelationType {
  return KNOWN_RELATIONS.has(relation) ? (relation as RelationType) : "related";
}

/** The busiest records keep their labels at every zoom; the rest appear on approach. */
const ALWAYS_LABELLED = 6;
const LABEL_ZOOM = 1.6;
const LABEL_CHARS = 32;

function clip(title: string): string {
  return title.length > LABEL_CHARS ? `${title.slice(0, LABEL_CHARS - 1)}…` : title;
}

function Legend({
  types,
  relations,
  hidden,
  onToggle,
}: {
  types: NodeType[];
  relations: RelationType[];
  hidden: Set<RelationType>;
  onToggle: (relation: RelationType) => void;
}) {
  return (
    <div className="mm-legend" aria-label="Legend">
      <ul className="mm-legend-row">
        {types.map((type) => (
          <li key={type}>
            <svg width="12" height="12" aria-hidden>
              <circle cx="6" cy="6" r="5" fill={TYPE_COLOURS[type]} />
            </svg>
            {NODE_TYPE_LABELS[type]}
          </li>
        ))}
        <li>
          <svg width="12" height="12" aria-hidden>
            <circle cx="6" cy="6" r="4.5" className="mm-swatch-discredited" />
          </svg>
          Discredited
        </li>
      </ul>
      {relations.length > 0 && (
        <ul className="mm-legend-row" aria-label="Link types">
          {relations.map((relation) => {
            const style = RELATION_STYLES[relation];
            const shown = !hidden.has(relation);
            return (
              <li key={relation}>
                <button
                  type="button"
                  className="mm-link-toggle"
                  aria-pressed={shown}
                  title={shown ? `Hide ${style.label} links` : `Show ${style.label} links`}
                  onClick={() => onToggle(relation)}
                >
                  <svg width="22" height="8" aria-hidden>
                    <line
                      x1="1"
                      y1="4"
                      x2="21"
                      y2="4"
                      stroke={style.colour}
                      strokeWidth="2"
                      strokeDasharray={style.dash}
                    />
                  </svg>
                  {style.label}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <p>
        Size is how many links a record has. Linked records pull together, so clusters are real
        connections — but distance is not similarity.
      </p>
    </div>
  );
}

export function MemoryMap({
  nodes,
  relations,
  selectedId,
  onSelect,
}: {
  nodes: GraphNode[];
  relations: GraphRelation[];
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const zoomRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const [transform, setTransform] = useState<ZoomTransform>(zoomIdentity);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<RelationType>>(() => new Set());

  const layout = useMemo(() => layoutMemoryMap(nodes, relations), [nodes, relations]);
  const visible = useMemo(
    () => relations.filter((edge) => !hidden.has(relationKey(edge.relation_type))),
    [relations, hidden],
  );
  const labelled = useMemo(() => {
    const ranked = [...layout.points.values()]
      .filter((point) => point.degree > 0)
      .sort((a, b) => b.degree - a.degree)
      .slice(0, ALWAYS_LABELLED);
    return new Set(ranked.map((point) => point.id));
  }, [layout]);

  const focusId = hoverId ?? selectedId;
  const lit = useMemo(
    () => (focusId ? neighbourhood(focusId, visible) : null),
    [focusId, visible],
  );

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const behaviour = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 8])
      .on("zoom", (event: { transform: ZoomTransform }) => setTransform(event.transform));
    select(svg).call(behaviour);
    zoomRef.current = behaviour;
    return () => {
      select(svg).on(".zoom", null);
    };
  }, []);

  // A new window of records is a new picture: start it framed.
  useEffect(() => {
    const svg = svgRef.current;
    if (svg && zoomRef.current) select(svg).call(zoomRef.current.transform, zoomIdentity);
  }, [layout]);

  const zoomBy = (factor: number) => {
    const svg = svgRef.current;
    if (svg && zoomRef.current) select(svg).call(zoomRef.current.scaleBy, factor);
  };
  const fit = () => {
    const svg = svgRef.current;
    if (svg && zoomRef.current) select(svg).call(zoomRef.current.transform, zoomIdentity);
  };

  // The busiest records, the one in focus and its neighbours are named; the
  // rest are named once the view is zoomed in far enough to fit them.
  const showLabel = (id: string) =>
    id === selectedId ||
    labelled.has(id) ||
    (lit !== null ? lit.has(id) : false) ||
    transform.k >= LABEL_ZOOM;

  const onKey = (event: KeyboardEvent, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(id);
    }
  };

  const drawn = new Set(relations.map((edge) => relationKey(edge.relation_type)));
  const legendRelations = RELATION_TYPES.filter((relation) => drawn.has(relation));
  const types = [...new Set(nodes.map((node) => node.node_type))];
  const { viewBox } = layout;
  const byId = new Map(nodes.map((node) => [node.id, node]));

  return (
    <div className="mm">
      <div className="mm-canvas" data-testid="knowledge-canvas">
        <svg
          ref={svgRef}
          className="mm-svg"
          viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
          preserveAspectRatio="xMidYMid meet"
          role="group"
          aria-label={`Memory map: ${nodes.length} records, ${relations.length} links`}
        >
          <g transform={transform.toString()}>
            <g>
              {visible.map((edge) => {
                const from = layout.points.get(edge.source_id);
                const to = layout.points.get(edge.target_id);
                if (!from || !to) return null;
                const style = RELATION_STYLES[relationKey(edge.relation_type)];
                const dim = focusId !== null && edge.source_id !== focusId && edge.target_id !== focusId;
                return (
                  <line
                    key={edge.id}
                    data-testid="mm-edge"
                    data-relation={edge.relation_type}
                    className={`mm-edge${dim ? " mm-dim" : ""}`}
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    stroke={style.colour}
                    strokeDasharray={style.dash}
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
            </g>
            <g>
              {[...layout.points.values()].map((point) => {
                const record = byId.get(point.id);
                if (!record) return null;
                const selected = point.id === selectedId;
                const dim = lit !== null && !lit.has(point.id);
                const links = `${point.degree} ${point.degree === 1 ? "link" : "links"}`;
                return (
                  <g
                    key={point.id}
                    data-testid="mm-node"
                    data-node-id={point.id}
                    className={`mm-node${dim ? " mm-dim" : ""}${selected ? " mm-node--selected" : ""}${
                      record.discredited ? " mm-node--discredited" : ""
                    }`}
                    transform={`translate(${point.x},${point.y})`}
                    role="button"
                    tabIndex={0}
                    aria-pressed={selected}
                    aria-label={`${record.title} — ${NODE_TYPE_LABELS[record.node_type]}, ${links}${
                      record.discredited ? ", discredited" : ""
                    }`}
                    onClick={() => onSelect(point.id)}
                    onKeyDown={(event) => onKey(event, point.id)}
                    onMouseEnter={() => setHoverId(point.id)}
                    onMouseLeave={() => setHoverId(null)}
                    onFocus={() => setHoverId(point.id)}
                    onBlur={() => setHoverId(null)}
                  >
                    {selected && <circle className="mm-ring" r={point.r + 5} />}
                    <circle
                      className="mm-dot"
                      r={point.r}
                      fill={record.discredited ? "none" : TYPE_COLOURS[record.node_type]}
                      stroke={TYPE_COLOURS[record.node_type]}
                    />
                    <title>{record.title}</title>
                  </g>
                );
              })}
            </g>
            {/* Labels last, so no dot is ever drawn over a name. */}
            <g aria-hidden>
              {[...layout.points.values()].map((point) => {
                const record = byId.get(point.id);
                if (!record || !showLabel(point.id)) return null;
                const dim = lit !== null && !lit.has(point.id);
                return (
                  <text
                    key={point.id}
                    className={`mm-label${dim ? " mm-dim" : ""}${
                      point.id === selectedId ? " mm-label--selected" : ""
                    }`}
                    x={point.x}
                    y={point.y + point.r + 13}
                    textAnchor="middle"
                  >
                    {clip(record.title)}
                  </text>
                );
              })}
            </g>
          </g>
        </svg>
        <div className="mm-controls">
          <button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => zoomBy(1.4)}>
            +
          </button>
          <button type="button" aria-label="Zoom out" title="Zoom out" onClick={() => zoomBy(1 / 1.4)}>
            −
          </button>
          <button type="button" aria-label="Fit to view" title="Fit to view" onClick={fit}>
            ⤢
          </button>
        </div>
      </div>
      <Legend
        types={types}
        relations={legendRelations}
        hidden={hidden}
        onToggle={(relation) =>
          setHidden((current) => {
            const next = new Set(current);
            if (next.has(relation)) next.delete(relation);
            else next.add(relation);
            return next;
          })
        }
      />
    </div>
  );
}
