import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../../api/http";
import type { LibraryDetail, LibraryPass, LibraryTicket } from "../../api/referenceLibraryApi";
import { api } from "../../api/client";
import { memoryPath, ticketPath } from "../../lib/appNavigation";
import { recordTitle } from "../../lib/memoryInferred";
import {
  STATE_HINTS,
  STATE_LABELS,
  filterTickets,
  groupTickets,
  licenceLabel,
  shortDate,
} from "../../lib/referenceLibrary";
import { describeError, pushToast } from "../../state/toastStore";
import { useUiStore } from "../../state/uiStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { ReferenceSourceInitiatives } from "./ReferenceSourceInitiatives";
import { ReferenceSourceUpstream } from "./ReferenceSourceUpstream";
import { TicketLink } from "../TicketLink";

/** Lists past this many rows show the first ones and say how many more there are. */
const PREVIEW = 12;
const PASS_PREVIEW = 5;

function NextStep({ detail }: { detail: LibraryDetail }) {
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const { state } = detail.source;
  if (state === "awaiting_approval") {
    return (
      <div className="rl-next">
        <p>Nothing in it is read until you approve it.</p>
        <Button variant="primary" compact onClick={() => setInboxOpen(true)}>
          Review in inbox
        </Button>
      </div>
    );
  }
  const running = detail.passes.find((p) => p.status === "dispatched");
  if (state === "reading" && running?.carrier) {
    return (
      <div className="rl-next">
        <p>Pass {running.ordinal} is reading it now.</p>
        <Link className="btn-secondary btn-compact" to={ticketPath(running.carrier)}>
          Open the pass’s ticket
        </Link>
      </div>
    );
  }
  if (state === "refused" && detail.refused_reason) {
    return <p className="rl-next rl-next--quiet">Refused: {detail.refused_reason}</p>;
  }
  return null;
}

function TicketList({ label, tickets }: { label: string; tickets: LibraryTicket[] }) {
  const [all, setAll] = useState(false);
  if (tickets.length === 0) return null;
  const shown = all ? tickets : tickets.slice(0, PREVIEW);
  return (
    <div className="rl-group">
      <h4 className="rl-group-title">
        {label} <span className="count-pill">{tickets.length}</span>
      </h4>
      <ul className="rl-tickets">
        {shown.map((ticket) => (
          <li key={ticket.external_id}>
            <TicketLink className="rl-ticket-link" ticketId={ticket.external_id}>
              <span className="rl-ticket-id">{ticket.external_id}</span> {ticket.title}
            </TicketLink>
            <span className="rl-tags">
              {ticket.state.replace("_", " ")} · {ticket.actions.join(", ")} · pass {ticket.passes.join(", ")}
            </span>
          </li>
        ))}
      </ul>
      {tickets.length > PREVIEW ? (
        <Button variant="plain" compact className="rl-more" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${tickets.length}`}
        </Button>
      ) : null}
    </div>
  );
}

function Tickets({ detail }: { detail: LibraryDetail }) {
  const [search, setSearch] = useState("");
  const filtered = filterTickets(detail.tickets, search);
  const { open, settled } = groupTickets(filtered);
  return (
    <section className="rl-section" aria-labelledby="rl-tickets-heading">
      <h3 id="rl-tickets-heading">
        Tickets it shaped <span className="count-pill">{detail.tickets.length}</span>
      </h3>
      {detail.tickets.length === 0 ? (
        <p className="rl-muted">
          {detail.source.state === "surveyed"
            ? "No ticket is linked: the old record kept only this source’s outcome, not the tickets it touched."
            : "No ticket yet. Findings land on tickets as its passes run."}
        </p>
      ) : (
        <>
          {detail.tickets.length > PREVIEW ? (
            <Input
              type="search"
              className="rl-ticket-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Filter tickets by id or title"
              aria-label="Filter this source's tickets"
            />
          ) : null}
          {filtered.length === 0 ? (
            <p className="rl-muted">No ticket matches “{search.trim()}”.</p>
          ) : (
            <>
              <TicketList label="Still open" tickets={open} />
              <TicketList label="Settled" tickets={settled} />
            </>
          )}
        </>
      )}
      {detail.unresolved_refs.length > 0 ? (
        <details className="rl-details">
          <summary>
            {detail.unresolved_refs.length} reference{detail.unresolved_refs.length === 1 ? "" : "s"} from the old
            record matched no ticket
          </summary>
          <p className="rl-muted">
            The old record named these by bare number without a workspace, so they were kept as written rather than
            guessed: {detail.unresolved_refs.join(", ")}.
          </p>
        </details>
      ) : null}
    </section>
  );
}

function Learnings({ detail }: { detail: LibraryDetail }) {
  const { availability, items, reason } = detail.learnings;
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, PREVIEW);
  return (
    <section className="rl-section" aria-labelledby="rl-learnings-heading">
      <h3 id="rl-learnings-heading">
        Learnings on those tickets <span className="count-pill">{items.length}</span>
      </h3>
      {availability === "unavailable" ? (
        <p className="rl-warn" role="status">
          Could not read learnings: {reason}. This is not the same as having none.
        </p>
      ) : availability === "empty" ? (
        <p className="rl-muted">{reason}</p>
      ) : (
        <>
          <ul className="rl-learnings">
            {shown.map((learning) => (
              <li key={learning.id}>
                <Link className="rl-learning-link" to={memoryPath("map", learning.id)}>
                  {recordTitle(learning)}
                </Link>
                <span className="rl-tags">
                  {learning.ticket}
                  {learning.discredited ? " · discredited" : ""}
                </span>
              </li>
            ))}
          </ul>
          {items.length > PREVIEW ? (
            <Button variant="plain" compact className="rl-more" aria-expanded={all} onClick={() => setAll((v) => !v)}>
              {all ? "Show fewer" : `Show all ${items.length}`}
            </Button>
          ) : null}
          {reason ? <p className="rl-warn">Some workspaces could not be read: {reason}.</p> : null}
        </>
      )}
    </section>
  );
}

function passLine(pass: LibraryPass): string {
  const parts = [pass.mode, pass.status];
  if (pass.finished_at) parts.push(shortDate(pass.finished_at));
  if (pass.files_examined > 0) parts.push(`read ${pass.files_read} of ${pass.files_examined} files`);
  return parts.join(" · ");
}

function Passes({ passes }: { passes: LibraryPass[] }) {
  const [all, setAll] = useState(false);
  const newestFirst = [...passes].reverse();
  const shown = all ? newestFirst : newestFirst.slice(0, PASS_PREVIEW);
  return (
    <section className="rl-section" aria-labelledby="rl-passes-heading">
      <h3 id="rl-passes-heading">
        Passes <span className="count-pill">{passes.length}</span>
      </h3>
      {passes.length === 0 ? (
        <p className="rl-muted">No pass has been queued.</p>
      ) : (
        <>
          <ol className="rl-passes" reversed start={passes.length}>
            {shown.map((pass) => (
              <li key={pass.ordinal}>
                <span className="rl-pass-title">
                  Pass {pass.ordinal}
                  {pass.label && pass.label !== `PASS ${pass.ordinal}` ? ` (${pass.label})` : ""}
                </span>
                <span className="rl-tags">{passLine(pass)}</span>
                {pass.outcome ? (
                  <span className="rl-pass-outcome">
                    {pass.outcome}: {pass.outcome_note}
                  </span>
                ) : null}
                {pass.stopped_at ? <span className="rl-pass-outcome">Stopped: {pass.stopped_at}</span> : null}
                {pass.refused_reason ? <span className="rl-pass-outcome">Refused: {pass.refused_reason}</span> : null}
              </li>
            ))}
          </ol>
          {passes.length > PASS_PREVIEW ? (
            <Button variant="plain" compact className="rl-more" aria-expanded={all} onClick={() => setAll((v) => !v)}>
              {all ? "Show the latest" : `Show all ${passes.length} passes`}
            </Button>
          ) : null}
        </>
      )}
    </section>
  );
}

function Facts({ detail }: { detail: LibraryDetail }) {
  const { coverage } = detail;
  return (
    <dl className="rl-facts">
      <dt>Licence</dt>
      <dd>
        {licenceLabel(detail.source)}
        {detail.licence_constraint ? <span className="rl-muted"> — {detail.licence_constraint}</span> : null}
      </dd>
      <dt>Serves</dt>
      <dd>
        <ul className="rl-plain">
          {detail.workspaces.map((ws) => (
            <li key={ws.slug}>
              <strong>{ws.slug}</strong>
              {ws.question && !ws.question.startsWith("(not recorded") ? ` — ${ws.question}` : ""}
            </li>
          ))}
        </ul>
      </dd>
      {detail.queued_reason && detail.queued_reason !== "imported from scratch/ref_projects" ? (
        <>
          <dt>Why queued</dt>
          <dd>{detail.queued_reason}</dd>
        </>
      ) : null}
      <ReferenceSourceUpstream detail={detail} />
      <dt>Read at</dt>
      <dd>{detail.pinned_commit ? <code>{detail.pinned_commit.slice(0, 12)}</code> : "Not recorded"}</dd>
      <dt>Coverage</dt>
      <dd>
        {coverage.mapped_commit
          ? `${coverage.read} of ${coverage.tree_files} files read in full, ${coverage.partial} in partly read folders, ${coverage.unopened} never opened`
          : detail.passes.length > 0
            ? "Not mapped: its passes predate the directory map, so what they read is in their notes; the next pass maps the tree."
            : "Not mapped yet: the tree is mapped when its first pass is dispatched."}
      </dd>
    </dl>
  );
}

function Body({ detail }: { detail: LibraryDetail }) {
  return (
    <>
      <NextStep detail={detail} />
      <Facts detail={detail} />
      <Tickets detail={detail} />
      <ReferenceSourceInitiatives detail={detail} />
      <Learnings detail={detail} />
      <Passes passes={detail.passes} />
      {detail.open_unknowns.length > 0 ? (
        <section className="rl-section" aria-labelledby="rl-unknowns-heading">
          <h3 id="rl-unknowns-heading">
            Still open <span className="count-pill">{detail.open_unknowns.length}</span>
          </h3>
          <ul className="rl-plain">
            {detail.open_unknowns.map((unknown) => (
              <li key={unknown.id}>
                {unknown.question} <span className="rl-muted">— needs {unknown.authority_required}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {detail.findings.length > 0 ? (
        <section className="rl-section" aria-labelledby="rl-findings-heading">
          <h3 id="rl-findings-heading">
            Findings <span className="count-pill">{detail.findings.length}</span>
          </h3>
          <ul className="rl-plain rl-findings">
            {detail.findings.map((finding) => (
              <li key={finding.id} className={finding.status === "retracted" ? "rl-retracted" : undefined}>
                {finding.line.replace(/\*\*/g, "").replace(/~~/g, "")}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {detail.notes ? (
        <details className="rl-details">
          <summary>Notes from the old record</summary>
          <p className="rl-notes">
            {detail.notes}
            {detail.notes_truncated ? "…" : ""}
          </p>
        </details>
      ) : null}
    </>
  );
}

/**
 * One source: what to do next, the facts that decide it, and what it changed.
 * Focus moves to its heading when it opens; Escape or "Back to sources"
 * closes it and returns to the list.
 */
export function ReferenceSourceDetail({ sourceId, onClose }: { sourceId: string; onClose: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  const source = useQuery({
    queryKey: ["reference-source", sourceId],
    queryFn: () => api.referenceSource(sourceId),
  });
  const missing = source.error instanceof ApiError && source.error.status === 404; // ts-org: allow-instanceof - a 404 is a different state from a failure, not an error to describe

  useEffect(() => {
    if (source.isError && !missing) {
      pushToast({
        tone: "error",
        title: "Source did not load",
        message: describeError(source.error, "the source request failed"),
      });
    }
  }, [source.isError, source.error, missing]);

  useEffect(() => {
    if (source.data) heading.current?.focus();
  }, [source.data]);

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    }
  }

  let body;
  if (source.isPending) {
    body = <PaneSkeleton variant="block" label="Loading the source…" />;
  } else if (missing) {
    body = <p className="rl-state">This source is no longer in the library.</p>;
  } else if (source.isError || !source.data) {
    body = (
      <div className="rl-state" role="alert">
        <p>The source did not load: {describeError(source.error, "the request failed")}.</p>
        <Button variant="secondary" compact disabled={source.isFetching} onClick={() => void source.refetch()}>
          {source.isFetching ? "Retrying…" : "Retry"}
        </Button>
      </div>
    );
  } else {
    body = <Body detail={source.data} />;
  }

  const detail = source.data;
  return (
    <section className="rl-detail" aria-labelledby="rl-detail-heading" onKeyDown={onKeyDown}>
      <div className="rl-detail-head">
        <Button variant="plain" compact className="rl-back" onClick={onClose}>
          ← Back to sources
        </Button>
        <h2 id="rl-detail-heading" ref={heading} tabIndex={-1}>
          {detail ? detail.source.name : "Source"}
        </h2>
        {detail ? (
          <>
            <p className="rl-detail-meta">
              <span className={`rl-state-badge rl-state-badge--${detail.source.state}`}>
                {STATE_LABELS[detail.source.state]}
              </span>{" "}
              <a href={detail.source.url} target="_blank" rel="noopener noreferrer" className="rl-external">
                {detail.source.url.replace(/^https?:\/\//, "")}
                <span className="rl-sr-only"> (opens in a new tab)</span>
              </a>
            </p>
            <p className="rl-muted">{STATE_HINTS[detail.source.state]}</p>
          </>
        ) : null}
      </div>
      <div className="rl-detail-body">{body}</div>
    </section>
  );
}
