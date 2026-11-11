import { useState } from "react";
import { Link } from "react-router-dom";

import type { ResearchClaim, ResearchProject } from "../../api/referenceResearchApi";
import { memoryPath } from "../../lib/appNavigation";
import { shortDate } from "../../lib/referenceLibrary";
import { CLAIM_LABELS, claimEvidence } from "../../lib/researchCandidates";
import { Button } from "../ui/Button";
import { QueuePicksModal } from "./QueuePicksModal";
import { RefuteClaimModal } from "./RefuteClaimModal";
import "./Research.css";

function Evidence({ claim }: { claim: ResearchClaim }) {
  const where = claim.evidence_path
    ? `${claim.evidence_path}${claim.evidence_start ? `:${claim.evidence_start}–${claim.evidence_end}` : ""} @ ${claim.evidence_commit.slice(0, 12)}`
    : claim.evidence_url
      ? "Doc page"
      : "";
  if (!where) return <span className="rs-claim--none">—</span>;
  return claim.evidence_url ? (
    <a href={claim.evidence_url} target="_blank" rel="noreferrer" title={claimEvidence(claim)}>
      {where}
    </a>
  ) : (
    <span title={claimEvidence(claim)}>{where}</span>
  );
}

/**
 * What research knows about one project: a row per capability it was checked
 * for, across every question, with the evidence and a way to correct it. Used
 * by the `research_project` card and a library source's Capabilities section.
 */
export function ResearchProjectView({
  project,
  onSourcePage = false,
}: {
  project: ResearchProject;
  /** The source page already names the project and is the library entry, so it shows neither. */
  onSourcePage?: boolean;
}) {
  const [refuting, setRefuting] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);
  const name = project.name || project.url;
  const latestQuestion = project.questions[0] ?? null;
  const facts = [
    project.licence_name,
    project.language,
    project.stars != null ? `${project.stars} stars` : "",
    project.last_commit_at ? `last commit ${shortDate(project.last_commit_at)}` : "",
  ].filter(Boolean);

  return (
    <div>
      {onSourcePage ? null : (
        <p className="rs-claim-head">
          <a href={project.url} target="_blank" rel="noreferrer">
            {name}
          </a>
          {facts.length ? <span className="rs-summary rs-inline">{facts.join(" · ")}</span> : null}
        </p>
      )}
      {project.claims.length ? (
        <div className="rs-table-scroll">
          <table className="rs-table" aria-label={`What research found about ${name}`}>
            <thead>
              <tr>
                <th scope="col" className="rs-cap">
                  Capability
                </th>
                <th scope="col">Claim</th>
                <th scope="col">Evidence</th>
                <th scope="col">Recorded</th>
                <th scope="col" aria-label="Correct it" />
              </tr>
            </thead>
            <tbody>
              {project.claims.map((claim) => (
                <tr key={claim.id}>
                  <th scope="row" data-label="Capability">
                    {claim.capability}
                    {claim.note ? <span className="rs-summary">{claim.note}</span> : null}
                  </th>
                  <td data-label="Claim">
                    <span className={`rs-claim rs-claim--${claim.status}`}>{CLAIM_LABELS[claim.status]}</span>
                  </td>
                  <td data-label="Evidence">
                    <Evidence claim={claim} />
                  </td>
                  <td data-label="Recorded">
                    {claim.recorded_by === "operator" ? "A person" : "Research"}, {shortDate(claim.created_at)}
                  </td>
                  <td data-label="Correct it">
                    {claim.status === "refuted" && claim.recorded_by === "operator" ? null : (
                      <Button
                        variant="plain"
                        compact
                        aria-haspopup="dialog"
                        aria-expanded={refuting === claim.capability}
                        aria-label={`Refute: ${name} ${claim.capability}`}
                        onClick={() => setRefuting(claim.capability)}
                      >
                        Refute…
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="rs-empty">
          Research has not checked anything about this project yet. Ask Baxter in a research thread.
        </p>
      )}
      <div className="rs-claim-actions">
        {onSourcePage ? null : project.source_id ? (
          <Link className="rs-tag" to={memoryPath("library", project.source_id)}>
            In library
          </Link>
        ) : latestQuestion ? (
          <Button variant="secondary" aria-haspopup="dialog" aria-expanded={queueing} onClick={() => setQueueing(true)}>
            Queue for review…
          </Button>
        ) : null}
      </div>
      <RefuteClaimModal
        open={refuting !== null}
        url={project.url}
        capability={refuting ?? ""}
        name={name}
        onClose={() => setRefuting(null)}
      />
      {latestQuestion && !onSourcePage ? (
        <QueuePicksModal
          open={queueing}
          picks={[{ url: project.url, name }]}
          questionId={latestQuestion.id}
          question={latestQuestion.question}
          onClose={() => setQueueing(false)}
        />
      ) : null}
    </div>
  );
}
