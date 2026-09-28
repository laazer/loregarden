/**
 * What the map is showing, in words, beside it — until a record is selected,
 * when the record's own panel takes this place.
 *
 * Every figure is computed from the records and links already on screen: this
 * rail fetches nothing, so it can never disagree with the map it describes.
 * Contradictions come first because they are the ones a person should act on.
 */

import type { GraphNode, GraphRelation, InferredGroup } from "../../api/memoryApi";
import { INFERRED_KIND_LABELS, inferredEdges, recordTitle } from "../../lib/memoryInferred";
import { graphShape } from "../../lib/memoryMapLayout";

const LIST_LIMIT = 5;

function RecordButton({
  node,
  onSelect,
}: {
  node: GraphNode | undefined;
  onSelect: (nodeId: string) => void;
}) {
  if (!node) return null;
  return (
    <button type="button" className="kb-link-button" onClick={() => onSelect(node.id)}>
      {recordTitle(node)}
    </button>
  );
}

export function MapOverview({
  nodes,
  relations,
  inferred,
  truncated,
  onSelect,
}: {
  nodes: GraphNode[];
  relations: GraphRelation[];
  inferred: InferredGroup[];
  truncated: boolean;
  onSelect: (nodeId: string) => void;
}) {
  // Recorded and inferred together: a record grouped with others by its ticket
  // is not "unlinked" to anyone reading the map, whatever the edge table says.
  const shape = graphShape(nodes, [...relations, ...inferredEdges(inferred)]);
  const groups = [...inferred].sort((a, b) => b.node_ids.length - a.node_ids.length);
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const contradictions = relations.filter((edge) => edge.relation_type === "contradicts");
  const busiest = shape.mostConnected.slice(0, LIST_LIMIT);
  const maxDegree = busiest[0]?.degree ?? 1;

  return (
    <aside className="mm-overview" aria-label="Map overview">
      <dl className="mm-figures">
        <div>
          <dt>Records</dt>
          <dd>{nodes.length}</dd>
        </div>
        <div>
          <dt>Recorded links</dt>
          <dd>{relations.length}</dd>
        </div>
        <div>
          <dt>Groups</dt>
          <dd>{inferred.length}</dd>
        </div>
        <div>
          <dt>Clusters</dt>
          <dd>{shape.clusters}</dd>
        </div>
        <div>
          <dt>Unlinked</dt>
          <dd>{shape.unlinked.length}</dd>
        </div>
      </dl>
      {truncated && (
        <p className="kb-muted">
          Showing the newest {nodes.length} records. Filter by type or text to see others.
        </p>
      )}

      <section aria-labelledby="mm-groups">
        <h3 id="mm-groups" className="kb-section-title">
          Groups
        </h3>
        {groups.length === 0 ? (
          <p className="kb-muted">No two records here share a ticket, milestone or specific tag.</p>
        ) : (
          <ul className="mm-list mm-groups">
            {groups.slice(0, LIST_LIMIT * 2).map((group) => (
              <li key={`${group.kind}:${group.key}`} className="mm-group">
                <details>
                  <summary>
                    <span>{group.label}</span>
                    <span className="mm-group-kind">
                      {INFERRED_KIND_LABELS[group.kind].toLowerCase()} · {group.node_ids.length}
                    </span>
                  </summary>
                  <ul>
                    {group.node_ids.map((id) => (
                      <li key={id}>
                        <RecordButton node={byId.get(id)} onSelect={onSelect} />
                      </li>
                    ))}
                  </ul>
                </details>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="mm-contradictions">
        <h3 id="mm-contradictions" className="kb-section-title">
          Contradictions
        </h3>
        {contradictions.length === 0 ? (
          <p className="kb-muted">No recorded contradictions among these records.</p>
        ) : (
          <ul className="mm-list">
            {contradictions.map((edge) => (
              <li key={edge.id} className="mm-contradiction">
                <RecordButton node={byId.get(edge.source_id)} onSelect={onSelect} />
                <span className="mm-versus" aria-label="contradicts">
                  ⟷
                </span>
                <RecordButton node={byId.get(edge.target_id)} onSelect={onSelect} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="mm-busiest">
        <h3 id="mm-busiest" className="kb-section-title">
          Most connected
        </h3>
        {busiest.length === 0 ? (
          <p className="kb-muted">No record is linked or grouped with another yet.</p>
        ) : (
          <ol className="mm-list">
            {busiest.map((entry) => (
              <li key={entry.id} className="mm-busiest">
                <RecordButton node={byId.get(entry.id)} onSelect={onSelect} />
                <span className="mm-degree">
                  <span
                    className="mm-degree-bar"
                    style={{ width: `${(entry.degree / maxDegree) * 100}%` }}
                    aria-hidden
                  />
                  <span>
                    {entry.degree} {entry.degree === 1 ? "connection" : "connections"}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section aria-labelledby="mm-unlinked">
        <h3 id="mm-unlinked" className="kb-section-title">
          Unlinked
        </h3>
        {shape.unlinked.length === 0 ? (
          <p className="kb-muted">Every record here is linked or grouped with another.</p>
        ) : (
          <>
            <p className="kb-muted">
              An unlinked record never appears in a briefing’s related-records digest.
            </p>
            <ul className="mm-list">
              {shape.unlinked.slice(0, LIST_LIMIT).map((id) => (
                <li key={id}>
                  <RecordButton node={byId.get(id)} onSelect={onSelect} />
                </li>
              ))}
            </ul>
            {shape.unlinked.length > LIST_LIMIT && (
              <p className="kb-muted">and {shape.unlinked.length - LIST_LIMIT} more</p>
            )}
          </>
        )}
      </section>
    </aside>
  );
}
