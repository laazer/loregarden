import type { UseQueryResult } from "@tanstack/react-query";

import type { CIStatus, WorkspacePullRequest, WorkspacePullRequests } from "../../api/workspaceDashboardApi";
import { formatRelativeAge } from "../../lib/timestamps";
import { isStale, pullRequestHeadline, TIER_LABELS } from "../../lib/workspaceDashboard";
import { DashboardSection } from "./DashboardSection";
import { MiniTicketCard } from "./MiniTicketCard";

const CI_LABELS: Record<CIStatus, string> = {
  passing: "Passing",
  failing: "Failing",
  pending: "Pending",
  partial: "Partial",
  skipped: "Skipped",
};

function ciLabel(row: WorkspacePullRequest): string {
  // `rollup` reads "no checks" as pending; to a person it is "nothing ran".
  return row.pull_request.checks.length === 0 ? "No checks" : CI_LABELS[row.ci];
}

function overlapLabel(row: WorkspacePullRequest): string {
  if (row.overlaps.length === 0) return "—";
  const named = row.overlaps
    .slice(0, 3)
    .map((o) => `#${o.number} (${o.shared_files})`)
    .join(", ");
  const more = row.overlaps.length > 3 ? ` +${row.overlaps.length - 3}` : "";
  return `${named}${more}${row.files_truncated ? " (at least)" : ""}`;
}

interface PullRequestsSectionProps {
  query: UseQueryResult<WorkspacePullRequests>;
  now: Date;
}

/**
 * "Where do PRs stand, and in what order do I review them?"
 *
 * Rows come in the server's review order (`services/workspace_pull_requests`):
 * tier, then fewest overlapping PRs, then smallest diff. The table is not
 * re-sortable, because the order is the answer; the Overlaps column shows why
 * a PR sits where it does.
 */
export function PullRequestsSection({ query, now }: PullRequestsSectionProps) {
  const rows = query.data?.pull_requests ?? [];
  return (
    <DashboardSection
      id="wsd-prs"
      title="Pull requests"
      count={query.data ? `${rows.length} open` : undefined}
      queries={[query]}
      errorTitle="Could not list pull requests from GitHub"
      empty={rows.length === 0}
      emptyMessage="No open pull requests. A ticket that finishes its workflow opens one here."
    >
      <p className="wsd-headline">{pullRequestHeadline(rows, now)}</p>
      {query.data?.truncated ? (
        <p className="wsd-note">Showing the first {rows.length}; GitHub has more open.</p>
      ) : null}
      <div className="wsd-table-wrap">
        <table className="wsd-table">
          <caption className="visually-hidden">Open pull requests in suggested review order</caption>
          <thead>
            <tr>
              <th scope="col" aria-sort="ascending" className="wsd-rank">
                Order
              </th>
              <th scope="col">Pull request</th>
              <th scope="col">Status</th>
              <th scope="col">Checks</th>
              <th scope="col" className="wsd-num">
                Size
              </th>
              <th scope="col">Overlaps with</th>
              <th scope="col">Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const pr = row.pull_request;
              return (
                <tr key={pr.number} data-tier={row.tier}>
                  <td className="wsd-rank" data-label="Order">
                    {row.review_rank}
                  </td>
                  <td data-label="Pull request">
                    <a href={pr.url} target="_blank" rel="noreferrer" className="wsd-row-link">
                      <span className="wsd-id">#{pr.number}</span> <span className="wsd-row-title">{pr.title}</span>
                    </a>
                    {row.tickets.length > 0 ? (
                      <span className="wsd-card-row">
                        {row.tickets.map((ticket) => (
                          <MiniTicketCard
                            key={ticket.id}
                            compact
                            ticket={{
                              id: ticket.id,
                              externalId: ticket.external_id,
                              title: ticket.title,
                              state: ticket.state,
                            }}
                          />
                        ))}
                      </span>
                    ) : null}
                  </td>
                  <td data-label="Status">
                    <span className={`wsd-tag wsd-tag--${row.tier}`}>{TIER_LABELS[row.tier]}</span>
                  </td>
                  <td data-label="Checks">
                    <span className={`wsd-ci--${pr.checks.length === 0 ? "none" : row.ci}`}>{ciLabel(row)}</span>
                  </td>
                  <td className="wsd-num" data-label="Size">
                    <span className="wsd-add">+{pr.additions}</span> <span className="wsd-del">−{pr.deletions}</span>
                  </td>
                  <td data-label="Overlaps with" className="wsd-muted wsd-mono">
                    {overlapLabel(row)}
                  </td>
                  <td data-label="Updated" className={isStale(row.updated_at, now) ? "wsd-stale wsd-mono" : "wsd-mono"}>
                    {formatRelativeAge(row.updated_at, now)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </DashboardSection>
  );
}
