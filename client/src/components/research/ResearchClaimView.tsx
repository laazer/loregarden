import { useState } from "react";

import type { ResearchClaimCard } from "../../api/referenceResearchApi";
import { shortDate } from "../../lib/referenceLibrary";
import { CLAIM_LABELS } from "../../lib/researchCandidates";
import { Button } from "../ui/Button";
import { RefuteClaimModal } from "./RefuteClaimModal";
import "./Research.css";

/**
 * One project and one capability: the verdict, who recorded it, and for a
 * verified claim the lines the server read from the file at that commit.
 */
export function ResearchClaimView({ card }: { card: ResearchClaimCard }) {
  const [refuting, setRefuting] = useState(false);
  const name = card.name || card.url;
  const claim = card.claim;
  const refutedByPerson = claim?.status === "refuted" && claim.recorded_by === "operator";

  return (
    <div className="rs-claim-card">
      <p className="rs-claim-head">
        <a href={card.url} target="_blank" rel="noreferrer">
          {name}
        </a>{" "}
        · <span className="rs-claim-capability">{card.capability}</span>
      </p>
      {claim ? (
        <>
          <p>
            <span className={`rs-claim rs-claim--${claim.status}`}>{CLAIM_LABELS[claim.status]}</span>{" "}
            <span className="rs-summary rs-inline">
              {claim.recorded_by === "operator" ? "Recorded by a person" : "Recorded by research"} on{" "}
              {shortDate(claim.created_at)}
              {card.earlier ? ` · ${card.earlier} earlier claim${card.earlier === 1 ? "" : "s"}` : ""}
            </span>
          </p>
          {claim.note ? <p className="rs-claim-note">{claim.note}</p> : null}
          {claim.evidence_excerpt ? (
            <figure className="rs-excerpt">
              <figcaption>
                {claim.evidence_url ? (
                  <a href={claim.evidence_url} target="_blank" rel="noreferrer">
                    {claim.evidence_path}:{claim.evidence_start}–{claim.evidence_end} @{" "}
                    {claim.evidence_commit.slice(0, 12)}
                  </a>
                ) : (
                  `${claim.evidence_path}:${claim.evidence_start}–${claim.evidence_end} @ ${claim.evidence_commit.slice(0, 12)}`
                )}
              </figcaption>
              <pre aria-label={`Lines ${claim.evidence_start} to ${claim.evidence_end} of ${claim.evidence_path}`}>
                {claim.evidence_excerpt.split("\n").map((line, index) => (
                  <span key={index}>
                    <span aria-hidden className="rs-excerpt-no">
                      {claim.evidence_start + index}
                    </span>
                    {line}
                    {"\n"}
                  </span>
                ))}
              </pre>
            </figure>
          ) : claim.evidence_url ? (
            <p className="rs-claim-note">
              <a href={claim.evidence_url} target="_blank" rel="noreferrer">
                Open the evidence
              </a>
            </p>
          ) : null}
        </>
      ) : (
        <p className="rs-empty">
          Nothing is recorded for this yet. Ask Baxter to check it in the project's source.
        </p>
      )}
      {refutedByPerson ? null : (
        <div className="rs-claim-actions">
          <Button variant="secondary" aria-haspopup="dialog" aria-expanded={refuting} onClick={() => setRefuting(true)}>
            Refute…
          </Button>
        </div>
      )}
      <RefuteClaimModal
        open={refuting}
        url={card.url}
        capability={card.capability}
        name={name}
        onClose={() => setRefuting(false)}
      />
    </div>
  );
}
