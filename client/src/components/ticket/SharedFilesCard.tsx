import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { TicketDetail } from "../../api/client";
import type { PathClaimSource, SiblingClaim } from "../../api/ticketEdgeApi";
import { ticketPath } from "../../lib/appNavigation";
import { ticketStateChip } from "../../lib/ticketStates";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { StatusChip } from "../ui/StatusChip";

import "./TicketLinksCard.css";
import "./SharedFilesCard.css";

/** Said in words, from the sibling's side: what it has done to the file. */
const SOURCE_TEXT: Record<PathClaimSource, string> = {
  observed: "changed",
  declared: "about to change",
  planned: "planned",
};

/** Paths shown per sibling before "Show all": the largest live overlap shares 11. */
const PATHS_SHOWN = 4;

export const siblingClaimsKey = (ticketId: string) => ["sibling-claims", ticketId] as const;

/**
 * "Which open sibling tickets will collide with this one, on which files, and
 * are they ordered?" — one row per sibling in the same landing tree that claims
 * a file this ticket claims. The name opens the sibling; "Wait for it" makes
 * this ticket wait until the sibling lands, so the two never edit side by side.
 */
export function SharedFilesCard({ ticket }: { ticket: TicketDetail }) {
  const headingId = useId();
  const claims = useQuery({
    queryKey: siblingClaimsKey(ticket.id),
    queryFn: () => api.siblingClaims(ticket.id),
    // The card renders its own failure, with a retry, where the answer would be.
    meta: { suppressErrorToast: true },
  });

  // Nothing while loading: one indexed read, well under the 200ms a skeleton is for.
  if (claims.isPending) return null;

  const unordered = claims.data?.siblings.filter((s) => !s.ordered).length ?? 0;

  return (
    <section className="tlk sfc" aria-labelledby={headingId}>
      <div className="tlk-head">
        <h2 id={headingId} className="tlk-title">
          Shared files
        </h2>
        {claims.data && claims.data.siblings.length > 0 ? (
          <span className="tlk-count">{claims.data.siblings.length}</span>
        ) : null}
        {unordered > 0 ? <span className="tlk-holding">{unordered} not ordered</span> : null}
      </div>
      {claims.isError ? (
        <div className="tlk-empty sfc-error" role="alert">
          {describeError(claims.error, "Could not check which siblings share this ticket's files")}.{" "}
          <Button variant="plain" className="tlk-more sfc-retry" onClick={() => void claims.refetch()}>
            Try again
          </Button>
        </div>
      ) : claims.data.claimed_paths === 0 ? (
        <p className="tlk-empty">
          Claims no files yet. Once its plan names the files it will change, open siblings in the same tree that
          claim any of them are listed here.
        </p>
      ) : claims.data.siblings.length === 0 ? (
        <p className="tlk-empty">
          No open sibling claims any of the {claims.data.claimed_paths} files this ticket claims.
        </p>
      ) : (
        <ul className="tlk-list">
          {claims.data.siblings.map((sibling) => (
            <SiblingRow key={sibling.ticket_id} ticketId={ticket.id} sibling={sibling} />
          ))}
        </ul>
      )}
    </section>
  );
}

function SiblingRow({ ticketId, sibling }: { ticketId: string; sibling: SiblingClaim }) {
  const qc = useQueryClient();
  const [showAll, setShowAll] = useState(false);
  const chip = ticketStateChip(sibling.state);
  const wait = useMutation({
    meta: { errorTitle: `Wait for ${sibling.external_id}` },
    mutationFn: () => api.addDependency(ticketId, sibling.ticket_id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: siblingClaimsKey(ticketId) });
      void qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      void qc.invalidateQueries({ queryKey: ["ticket-tree"] });
    },
  });
  const paths = showAll ? sibling.paths : sibling.paths.slice(0, PATHS_SHOWN);

  return (
    <li className="sfc-item">
      <div className="sfc-top">
        <Link className="sfc-name" to={ticketPath(sibling.ticket_id)}>
          {sibling.title}
        </Link>
        <StatusChip tone={chip.tone} label={chip.label} />
      </div>
      <div className="sfc-meta">
        <span className="tlk-id">{sibling.external_id}</span>
        {sibling.ordered ? (
          <span className="sfc-ordered">Ordered</span>
        ) : (
          <Button
            variant="secondary"
            compact
            disabled={wait.isPending}
            aria-label={`Wait for ${sibling.external_id} to land before this ticket runs`}
            onClick={() => wait.mutate()}
          >
            {wait.isPending ? "Linking…" : "Wait for it"}
          </Button>
        )}
      </div>
      <ul className="sfc-paths" aria-label={`Files shared with ${sibling.external_id}`}>
        {paths.map((claim) => (
          <li key={claim.path} className="sfc-path">
            <span className="sfc-path-name" title={claim.path}>
              {claim.path}
            </span>
            <span className={`sfc-source sfc-source--${claim.source}`}>{SOURCE_TEXT[claim.source]}</span>
          </li>
        ))}
      </ul>
      {sibling.paths.length > PATHS_SHOWN ? (
        <Button variant="plain" className="tlk-more" aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer" : `Show all ${sibling.paths.length} files`}
        </Button>
      ) : null}
    </li>
  );
}
