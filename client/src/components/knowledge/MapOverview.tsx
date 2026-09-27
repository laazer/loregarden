/**
 * What the map is showing, in words, beside it — until a record is selected,
 * when the record's own panel takes this place.
 *
 * Every figure is computed from the records and links already on screen: this
 * rail fetches nothing, so it can never disagree with the map it describes.
 * Contradictions come first because they are the ones a person should act on.
 */

import type { GraphNode, GraphRelation } from "../../api/memoryApi";
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
      {node.title}
    </button>
  );
}

export function MapOverview({
  nodes,
  relations,
  truncated,
  onSelect,
}: {
  nodes: GraphNode[];
  relations: GraphRelation[];
  truncated: boolean;
  onSelect: (nodeId: string) => void;
}) {
  const shape = graphShape(nodes, relations);
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
          <dt>Links</dt>
          <dd>{relations.length}</dd>
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
          <p className="kb-muted">No record has a recorded link yet.</p>
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
                    {entry.degree} {entry.degree === 1 ? "link" : "links"}
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
          <p className="kb-muted">Every record here is linked to another.</p>
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
