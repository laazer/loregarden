import { useState } from "react";
import { Link } from "react-router-dom";

import type { ResearchCandidate, ResearchRunDiff } from "../../api/referenceResearchApi";
import { memoryPath } from "../../lib/appNavigation";
import { Button } from "../ui/Button";
import { ClaimBadge } from "./ResearchCandidatesTable";
import { QueuePicksModal } from "./QueuePicksModal";
import "./Research.css";

function ProjectList({ label, rows }: { label: string; rows: ResearchCandidate[] }) {
  return (
    <ul className="rs-pick-list" aria-label={label}>
      {rows.map((row) => (
        <li key={row.url}>
          <a href={row.url} target="_blank" rel="noreferrer">
            {row.name}
          </a>
          {row.source_id ? (
            <Link className="rs-tag" to={memoryPath("library", row.source_id)}>
              In library
            </Link>
          ) : null}
          {row.summary ? <span className="rs-summary">{row.summary}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/**
 * What a run of a saved question changed: projects that appeared, projects that
 * are gone, and capabilities whose claim moved. New projects can go straight to
 * the review queue; everything else is in the question's candidates table.
 */
export function ResearchDiffView({ diff }: { diff: ResearchRunDiff }) {
  const [queueing, setQueueing] = useState(false);
  const picks = diff.added.filter((row) => !row.source_id).map((row) => ({ url: row.url, name: row.name }));
  const first = diff.previous_run === 0;
  const quiet = !first && diff.added.length === 0 && diff.dropped.length === 0 && diff.changed.length === 0;

  return (
    <div>
      {quiet ? (
        <p className="rs-empty">
          Nothing changed since run {diff.previous_run}
          {diff.unchanged ? `: ${diff.unchanged} claim${diff.unchanged === 1 ? "" : "s"} re-checked and found the same.` : "."}
        </p>
      ) : null}
      {diff.changed.length ? (
        <section className="rs-diff-section">
          <h4 className="rs-field-label">Changed claims</h4>
          <div className="rs-table-scroll">
            <table className="rs-table" aria-label={`Claims that changed in run ${diff.run}`}>
              <thead>
                <tr>
                  <th scope="col">Project</th>
                  <th scope="col" className="rs-cap">
                    Capability
                  </th>
                  <th scope="col">Run {diff.previous_run || "before"}</th>
                  <th scope="col">Run {diff.run}</th>
                </tr>
              </thead>
              <tbody>
                {diff.changed.map((change) => (
                  <tr key={`${change.url} ${change.capability}`}>
                    <th scope="row" data-label="Project">
                      <a href={change.url} target="_blank" rel="noreferrer">
                        {change.name}
                      </a>
                    </th>
                    <td data-label="Capability">{change.capability}</td>
                    <td data-label="Before">
                      <ClaimBadge claim={change.before} capability={change.capability} />
                    </td>
                    <td data-label="Now">
                      <ClaimBadge claim={change.after} capability={change.capability} />
                      {change.after.note ? <span className="rs-summary">{change.after.note}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      {diff.added.length ? (
        <section className="rs-diff-section">
          <h4 className="rs-field-label">{first ? `Put forward (${diff.added.length})` : `New this run (${diff.added.length})`}</h4>
          <ProjectList label={first ? "Projects put forward" : "Projects new this run"} rows={diff.added} />
          {picks.length ? (
            <Button variant="secondary" compact aria-haspopup="dialog" aria-expanded={queueing} onClick={() => setQueueing(true)}>
              Queue {picks.length} new for review…
            </Button>
          ) : null}
        </section>
      ) : null}
      {diff.dropped.length ? (
        <section className="rs-diff-section">
          <h4 className="rs-field-label">Gone this run ({diff.dropped.length})</h4>
          <p className="rs-summary">Run {diff.previous_run} put these forward; this run did not.</p>
          <ProjectList label="Projects gone this run" rows={diff.dropped} />
        </section>
      ) : null}
      {diff.dropped_known ? null : (
        <p className="rs-summary">
          A later run has replaced this one, so which projects it dropped is no longer known.
        </p>
      )}
      <QueuePicksModal
        open={queueing}
        picks={picks}
        questionId={diff.question_id}
        question={diff.question}
        onClose={() => setQueueing(false)}
      />
    </div>
  );
}
