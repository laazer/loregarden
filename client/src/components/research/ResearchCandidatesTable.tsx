import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import type { ResearchClaim, ResearchQuestion } from "../../api/referenceResearchApi";
import { memoryPath } from "../../lib/appNavigation";
import { shortDate } from "../../lib/referenceLibrary";
import {
  CLAIM_LABELS,
  claimEvidence,
  nextSort,
  sortCandidates,
  type CandidateSort,
  type CandidateSortKey,
} from "../../lib/researchCandidates";
import { Button } from "../ui/Button";
import "./Research.css";

/**
 * One claim: how far a capability was checked. A verified one links to the file
 * at the commit it was read at, so "verified" can always be opened and read.
 */
function ClaimBadge({ claim, capability }: { claim: ResearchClaim | null | undefined; capability: string }) {
  if (!claim) {
    return <span className="rs-claim rs-claim--none" title={`Nothing claimed for “${capability}”`}>Not checked</span>;
  }
  const label = CLAIM_LABELS[claim.status];
  const evidence = claimEvidence(claim);
  const className = `rs-claim rs-claim--${claim.status}`;
  if (claim.evidence_url) {
    return (
      <a
        className={className}
        href={claim.evidence_url}
        target="_blank"
        rel="noreferrer"
        title={evidence}
        aria-label={`${label}: ${capability}. ${evidence}. Opens the evidence.`}
      >
        {label}
      </a>
    );
  }
  return (
    <span className={className} title={evidence || undefined}>
      {label}
    </span>
  );
}

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
  numeric = false,
  className,
}: {
  label: string;
  sortKey: CandidateSortKey;
  sort: CandidateSort;
  onSort: (key: CandidateSortKey) => void;
  numeric?: boolean;
  className?: string;
}) {
  const active = sort.key === sortKey;
  return (
    <th scope="col" className={numeric ? "rs-num" : className} aria-sort={active ? sort.dir : "none"}>
      <Button variant="plain" className="rs-sort" onClick={() => onSort(sortKey)}>
        {label}
        <span aria-hidden className="rs-sort-mark">
          {active ? (sort.dir === "ascending" ? "↑" : "↓") : ""}
        </span>
      </Button>
    </th>
  );
}

/**
 * A research question's candidates, one row per project and a column per
 * capability, so "which of these does what I asked?" reads across a row.
 */
export function ResearchCandidatesTable({ question }: { question: ResearchQuestion }) {
  const [sort, setSort] = useState<CandidateSort>(() =>
    question.capabilities[0]
      ? { key: `capability:${question.capabilities[0]}`, dir: "descending" }
      : { key: "name", dir: "ascending" },
  );
  const rows = useMemo(() => sortCandidates(question.candidates, sort), [question.candidates, sort]);
  const onSort = (key: CandidateSortKey) => setSort((current) => nextSort(current, key));

  return (
    <div className="rs-table-scroll">
      <table className="rs-table" aria-label={`Candidates for: ${question.question}`}>
        <thead>
          <tr>
            <SortHeader label="Project" sortKey="name" sort={sort} onSort={onSort} />
            {question.capabilities.map((capability) => (
              <SortHeader
                key={capability}
                label={capability}
                sortKey={`capability:${capability}`}
                sort={sort}
                onSort={onSort}
                className="rs-cap"
              />
            ))}
            <th scope="col">Licence</th>
            <SortHeader label="Stars" sortKey="stars" sort={sort} onSort={onSort} numeric />
            <SortHeader label="Last commit" sortKey="last_commit" sort={sort} onSort={onSort} />
            <th scope="col">Language</th>
          </tr>
        </thead>
        <tbody>
          {/* ux-ok: the caller renders the empty state and never mounts this with no candidates. */}
          {rows.map((row) => (
            <tr key={row.id}>
              <th scope="row" className="rs-project" data-label="Project">
                <a href={row.url} target="_blank" rel="noreferrer" title={row.summary || row.url}>
                  {row.name}
                </a>
                {row.new ? <span className="rs-tag rs-tag--new">New</span> : null}
                {row.source_id ? (
                  <Link className="rs-tag" to={memoryPath("library", row.source_id)}>
                    In library
                  </Link>
                ) : null}
                {row.summary ? <span className="rs-summary">{row.summary}</span> : null}
              </th>
              {question.capabilities.map((capability) => (
                <td key={capability} data-label={capability}>
                  <ClaimBadge claim={row.claims[capability]} capability={capability} />
                </td>
              ))}
              <td data-label="Licence" title={row.metadata_error || undefined}>
                {row.licence_name || "—"}
              </td>
              <td className="rs-num" data-label="Stars">
                {row.stars ?? "—"}
              </td>
              <td data-label="Last commit">{shortDate(row.last_commit_at)}</td>
              <td data-label="Language">{row.language || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
