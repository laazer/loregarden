import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../../api/client";

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
import { pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { QueuePicksModal } from "./QueuePicksModal";
import { SaveQuestionModal, intervalLabel } from "./SaveQuestionModal";
import "./Research.css";

/**
 * One claim: how far a capability was checked. A verified one links to the file
 * at the commit it was read at, so "verified" can always be opened and read.
 */
export function ClaimBadge({ claim, capability }: { claim: ResearchClaim | null | undefined; capability: string }) {
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
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const [queueing, setQueueing] = useState(false);
  const [saving, setSaving] = useState(false);
  const queryClient = useQueryClient();
  // Errors reach a toast through the mutation cache's errorTitle.
  const rerun = useMutation({
    meta: { errorTitle: "Re-run research" },
    mutationFn: () => api.rerunResearchQuestion(question.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["baxter-chat-session"] });
      pushToast({ tone: "success", title: "Re-running", message: "The answer lands in this thread." });
    },
  });
  const picks = rows.filter((row) => selected.has(row.url)).map((row) => ({ url: row.url, name: row.name }));
  const toggle = (url: string, on: boolean) =>
    setSelected((current) => {
      const next = new Set(current);
      if (on) next.add(url);
      else next.delete(url);
      return next;
    });

  return (
    <>
    <div className="rs-table-toolbar">
      <span className="rs-summary rs-inline">
        {picks.length ? `${picks.length} selected` : "Select projects to queue them for a survey pass."}
      </span>
      <span className="rs-toolbar-actions">
      <Button variant="plain" compact disabled={rerun.isPending} onClick={() => rerun.mutate()}>
        {rerun.isPending ? "Starting…" : "Run again"}
      </Button>
      <Button
        variant="plain"
        compact
        aria-haspopup="dialog"
        aria-expanded={saving}
        title={question.schedule_error ? `Last scheduled re-run did not start: ${question.schedule_error}` : undefined}
        onClick={() => setSaving(true)}
      >
        {question.saved ? `Saved · ${intervalLabel(question.interval_days)}` : "Save question…"}
        {question.schedule_error ? " ⚠" : ""}
      </Button>
      <Button
        variant="secondary"
        compact
        disabled={picks.length === 0}
        aria-haspopup="dialog"
        aria-expanded={queueing}
        onClick={() => setQueueing(true)}
      >
        Queue {picks.length || ""} for review…
      </Button>
      </span>
    </div>
    <div className="rs-table-scroll">
      <table className="rs-table" aria-label={`Candidates for: ${question.question}`}>
        <thead>
          <tr>
            <th scope="col" aria-label="Select" />
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
              <td className="rs-select">
                <Input
                  type="checkbox"
                  checked={selected.has(row.url)}
                  disabled={Boolean(row.source_id)}
                  title={row.source_id ? "Already in the library" : undefined}
                  aria-label={row.source_id ? `${row.name} is already in the library` : `Select ${row.name}`}
                  onChange={(event) => toggle(row.url, event.target.checked)}
                />
              </td>
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
    <QueuePicksModal
      open={queueing}
      picks={picks}
      questionId={question.id}
      question={question.question}
      onClose={() => setQueueing(false)}
      onQueued={() => setSelected(new Set())}
    />
    {saving ? <SaveQuestionModal open question={question} onClose={() => setSaving(false)} /> : null}
    </>
  );
}
