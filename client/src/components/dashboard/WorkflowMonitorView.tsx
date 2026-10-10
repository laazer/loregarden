import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { MonitorFinding } from "../../api/types";
import { ticketPath } from "../../lib/appNavigation";
import {
  conditionLabel,
  conditionMeaning,
  groupByTicket,
  groupFindings,
  isAwaitingSamples,
  isLiveFinding,
  type FindingGroup,
  type TicketFindings,
} from "../../lib/monitorFindings";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import "./WorkflowMonitorView.css";

/**
 * Every current workflow-monitor finding, arranged around the question a
 * person opening it has: *what needs me, and where do I go?*
 *
 * The first version answered a different question — "what did the detectors
 * record?" — and printed 138 findings as one condition-grouped wall of
 * "Stage 'review' ran 4 times…", with no ticket named and no link. 85 of the
 * 94 ticket findings were on tickets that had finished weeks earlier.
 *
 * So, in order:
 * 1. **On this ticket** — when a ticket is selected, its own findings first.
 * 2. **Recurring** — a condition on the same stage across several tickets is a
 *    pipeline fault, not a ticket's; one row each, the tickets behind it.
 * 3. **By ticket** — every ticket with a finding, named and linked, one line
 *    per condition however many runs reported it.
 * 4. **Workspace-wide** — findings about a workspace rather than a ticket.
 *
 * "Needs attention" (the default) hides findings on done/won't-do tickets;
 * "Everything" brings them back for a post-mortem. Report-only, like the
 * monitor itself: no resolve or dismiss control.
 */

type Scope = "attention" | "all";

function Centred({ children }: { children: React.ReactNode }) {
  return <div className="wm-centred">{children}</div>;
}

function StatePill({ state }: { state: TicketFindings["state"] }) {
  if (!state) return <span className="wm-pill wm-pill-missing">ticket gone</span>;
  return <span className={`wm-pill wm-pill-${state}`}>{state.replace(/_/g, " ")}</span>;
}

function TicketCard({ ticket }: { ticket: TicketFindings }) {
  return (
    <li className="wm-ticket">
      <div className="wm-ticket-head">
        <Link className="wm-ticket-link" to={ticketPath(ticket.ticketId)}>
          <span className="wm-ticket-id">{ticket.externalId || ticket.ticketId.slice(0, 8)}</span>
          <span className="wm-ticket-title">{ticket.title || "Untitled or deleted ticket"}</span>
        </Link>
        <StatePill state={ticket.state} />
      </div>
      <ul className="wm-lines">
        {ticket.lines.map((line) => (
          <li key={line.key} className="wm-line">
            <span className="wm-line-condition">
              {conditionLabel(line.condition)}
              {line.stageKey && <code>{line.stageKey}</code>}
            </span>
            <span className="wm-line-summary">
              {line.summary}
              {line.count > 1 && <span className="wm-line-count"> · {line.count} runs</span>}
            </span>
          </li>
        ))}
      </ul>
      <div className="wm-ticket-foot">
        {ticket.workspaceSlug && <span>{ticket.workspaceSlug}</span>}
        {ticket.lastSeen && <span>last seen {formatRelativeAge(ticket.lastSeen)}</span>}
      </div>
    </li>
  );
}

function RecurringRow({ group, tickets }: { group: FindingGroup; tickets: TicketFindings[] }) {
  const meaning = conditionMeaning(group.condition);
  return (
    <li className="wm-recurring">
      <details>
        <summary>
          <span className="wm-recurring-title">
            {conditionLabel(group.condition)}
            {group.stageKey && <code>{group.stageKey}</code>}
          </span>
          <span className="wm-recurring-count">{group.tickets} tickets</span>
          {meaning && <span className="wm-meaning">{meaning}</span>}
        </summary>
        <ul className="wm-recurring-tickets">
          {tickets.map((ticket) => (
            <li key={ticket.ticketId}>
              <Link to={ticketPath(ticket.ticketId)}>
                <span className="wm-ticket-id">{ticket.externalId || ticket.ticketId.slice(0, 8)}</span>{" "}
                {ticket.title}
              </Link>{" "}
              <StatePill state={ticket.state} />
            </li>
          ))}
        </ul>
      </details>
    </li>
  );
}

function WorkspaceRow({ group }: { group: FindingGroup }) {
  const meaning = conditionMeaning(group.condition);
  const waiting = group.findings.filter(isAwaitingSamples);
  const actionable = group.findings.filter((finding) => !isAwaitingSamples(finding));
  const counts = [
    actionable.length > 0 && `${actionable.length} to act on`,
    waiting.length > 0 && `${waiting.length} awaiting data`,
  ].filter(Boolean);
  return (
    <li className="wm-recurring">
      <details>
        <summary>
          <span className="wm-recurring-title">{conditionLabel(group.condition)}</span>
          <span className="wm-recurring-count">{counts.join(" · ")}</span>
          {meaning && <span className="wm-meaning">{meaning}</span>}
        </summary>
        {actionable.length > 0 && (
          <ul className="wm-lines">
            {actionable.map((finding, index) => (
              <li key={index} className="wm-line">
                {finding.stage_key && (
                  <span className="wm-line-condition">
                    <code>{finding.stage_key}</code>
                  </span>
                )}
                <span className="wm-line-summary">{finding.summary}</span>
              </li>
            ))}
          </ul>
        )}
        {waiting.length > 0 && (
          <p className="wm-waiting">
            Not enough runs yet to judge {waiting.length === 1 ? "this stage" : `these ${waiting.length} stages`}:{" "}
            {waiting.map((finding) => finding.stage_key || "(workspace)").join(", ")}.
          </p>
        )}
      </details>
    </li>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="wm-section">
      <h3 className="wm-section-title">{title}</h3>
      {hint && <p className="wm-section-hint">{hint}</p>}
      {children}
    </section>
  );
}

function arrange(findings: MonitorFinding[], ticketId: string | null) {
  const ticketFindings = findings.filter((finding) => finding.ticket_id);
  const workspaceFindings = findings.filter((finding) => !finding.ticket_id);
  const byTicket = groupByTicket(ticketFindings);
  const ticketsById = new Map(byTicket.map((ticket) => [ticket.ticketId, ticket]));
  const recurring = groupFindings(ticketFindings)
    .filter((group) => group.tickets >= 2)
    .map((group) => ({
      group,
      tickets: [...new Set(group.findings.map((finding) => finding.ticket_id))]
        .map((id) => ticketsById.get(id))
        .filter((ticket): ticket is TicketFindings => Boolean(ticket)),
    }));
  return {
    current: ticketId ? (ticketsById.get(ticketId) ?? null) : null,
    others: byTicket.filter((ticket) => ticket.ticketId !== ticketId),
    recurring,
    // One row per condition — forty "timeout floor stale" rows, one per stage,
    // said the same thing forty times. Inside, WorkspaceRow separates the few
    // to act on from the many only awaiting data.
    workspace: groupFindings(workspaceFindings, { byStage: false }),
  };
}

export function WorkflowMonitorView({ ticketId = null }: { ticketId?: string | null }) {
  const [scope, setScope] = useState<Scope>("attention");
  const findings = useQuery({
    queryKey: ["monitor-findings", "all"],
    queryFn: () => api.monitorFindings(),
    refetchInterval: 30_000,
  });

  const all = useMemo(() => findings.data ?? [], [findings.data]);
  const live = useMemo(() => all.filter(isLiveFinding), [all]);
  const shown = scope === "attention" ? live : all;
  const view = useMemo(() => arrange(shown, ticketId), [shown, ticketId]);

  // Loading: the endpoint recomputes workspace conditions per request and can
  // take a few seconds — say so, rather than show a blank pane.
  if (findings.isPending) {
    return (
      <Centred>
        <div className="wm-muted">Loading findings… the monitor recomputes workspace checks on each request.</div>
      </Centred>
    );
  }

  // Error: named, with the reason, and visibly distinct from "nothing to
  // report" — a monitor that renders an empty list when it could not ask is
  // the exact failure this surface exists to stop.
  if (findings.isError) {
    return (
      <Centred>
        <div className="wm-error-title">Could not load monitor findings</div>
        <div className="wm-muted wm-narrow">
          {describeError(findings.error, "The monitor endpoint did not answer.")} Nothing is known about the
          pipeline&rsquo;s health until this succeeds — this is not the same as having nothing to report.
        </div>
        <button type="button" className="tab-btn" onClick={() => findings.refetch()} disabled={findings.isFetching}>
          {findings.isFetching ? "Retrying…" : "Retry"}
        </button>
      </Centred>
    );
  }

  if (!all.length) {
    return (
      <Centred>
        <div className="wm-empty-title">Nothing to report</div>
        <div className="wm-muted wm-narrow">
          The workflow monitor runs on the reconcile timer and records what it notices — a stage attempted far more
          than its baseline, a run still going long past what that stage has ever taken, a ticket cursor pointing at
          a stage its workflow does not have. Findings appear here as they are written.
        </div>
      </Centred>
    );
  }

  const finished = all.length - live.length;
  const nothingShown = !view.current && !view.others.length && !view.workspace.length;

  return (
    <div className="wm-root">
      <header className="wm-header">
        <div className="wm-scope" role="group" aria-label="Which findings to show">
          <button
            type="button"
            className={`wm-scope-btn${scope === "attention" ? " active" : ""}`}
            aria-pressed={scope === "attention"}
            onClick={() => setScope("attention")}
          >
            Needs attention <span className="wm-scope-count">{live.length}</span>
          </button>
          <button
            type="button"
            className={`wm-scope-btn${scope === "all" ? " active" : ""}`}
            aria-pressed={scope === "all"}
            onClick={() => setScope("all")}
          >
            Everything <span className="wm-scope-count">{all.length}</span>
          </button>
        </div>
        <button
          type="button"
          className="tab-btn"
          onClick={() => findings.refetch()}
          disabled={findings.isFetching}
          aria-label="Refresh monitor findings"
        >
          {findings.isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {nothingShown && (
        <Centred>
          <div className="wm-empty-title">Nothing on live tickets</div>
          <div className="wm-muted wm-narrow">
            Every current finding is on a ticket that has already finished ({finished} of them). Switch to
            &ldquo;Everything&rdquo; to review them.
          </div>
        </Centred>
      )}

      {view.current && (
        <Section title="On this ticket">
          <ul className="wm-tickets">
            <TicketCard ticket={view.current} />
          </ul>
        </Section>
      )}

      {view.recurring.length > 0 && (
        <Section
          title="Recurring across tickets"
          hint="The same condition on the same stage in several tickets points at the stage or its agent, not the tickets."
        >
          <ul className="wm-recurring-list">
            {view.recurring.map(({ group, tickets }) => (
              <RecurringRow key={group.key} group={group} tickets={tickets} />
            ))}
          </ul>
        </Section>
      )}

      {view.others.length > 0 && (
        <Section title={view.current ? "Other tickets" : "By ticket"}>
          <ul className="wm-tickets">
            {view.others.map((ticket) => (
              <TicketCard key={ticket.ticketId} ticket={ticket} />
            ))}
          </ul>
        </Section>
      )}

      {view.workspace.length > 0 && (
        <Section title="Workspace-wide" hint="About a workspace's configuration rather than any one ticket.">
          <ul className="wm-recurring-list">
            {view.workspace.map((group) => (
              <WorkspaceRow key={group.key} group={group} />
            ))}
          </ul>
        </Section>
      )}

      {scope === "attention" && finished > 0 && !nothingShown && (
        <p className="wm-footnote">
          {finished} more {finished === 1 ? "finding is" : "findings are"} on finished tickets — hidden. Choose
          &ldquo;Everything&rdquo; to include them.
        </p>
      )}
    </div>
  );
}
