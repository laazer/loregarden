import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";

import { api } from "../../api/client";
import type { MonitorFinding } from "../../api/types";
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
import { useHeldFailure } from "../../hooks/useHeldFailure";
import { useOperatorRefetch } from "../../hooks/useOperatorRefetch";
import { useRestoreFocusOnSettle } from "../../hooks/useRestoreFocusOnSettle";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError, endSentence } from "../../state/toastStore";
import "./WorkflowMonitorView.css";
import { TicketLink } from "../TicketLink";
import { Button } from "../ui/Button";
import { StaleReadNotice } from "../ui/StaleReadNotice";

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

function Centred({ children, role }: { children: React.ReactNode; role?: string }) {
  return (
    <div className="wm-centred" role={role}>
      {children}
    </div>
  );
}

function StatePill({ state }: { state: TicketFindings["state"] }) {
  if (!state) return <span className="wm-pill wm-pill-missing">ticket gone</span>;
  return <span className={`wm-pill wm-pill-${state}`}>{state.replace(/_/g, " ")}</span>;
}

function TicketCard({ ticket }: { ticket: TicketFindings }) {
  return (
    <li className="wm-ticket">
      <div className="wm-ticket-head">
        <TicketLink className="wm-ticket-link" ticketId={ticket.ticketId}>
          <span className="wm-ticket-id">{ticket.externalId || ticket.ticketId.slice(0, 8)}</span>
          <span className="wm-ticket-title">{ticket.title || "Untitled or deleted ticket"}</span>
        </TicketLink>
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
              <TicketLink ticketId={ticket.ticketId}>
                <span className="wm-ticket-id">{ticket.externalId || ticket.ticketId.slice(0, 8)}</span>{" "}
                {ticket.title}
              </TicketLink>{" "}
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

  // The hold that makes the failure branch below outlive its own Retry click:
  // query-core clears `error` and puts a failed-with-no-data query back to
  // pending on `refetch()`, so a branch guarded on `findings.isError` unmounted
  // into the loading branch the moment the operator pressed Retry. The subject
  // is the constant this query's key is pinned to — `["monitor-findings",
  // "all"]`, and `api.monitorFindings()` takes no argument — and emphatically
  // not `ticketId`, which feeds only `arrange()`. Keying it to the selection
  // would drop a still-true failure on a ticket switch that re-reads nothing,
  // and execution would fall through to `!all.length`: the monitor reporting
  // health it never asked about.
  const failure = useHeldFailure(findings, "all");
  // Native `disabled` blurs a focused <button> in a real browser, so a keyboard
  // operator who pressed Enter on Retry is dumped on <body> for the length of
  // the read, and nothing puts them back.
  //
  // The trigger is the operator's *activation*, not the `isFetching`
  // transition. `refetchInterval: 30_000` produces the identical true → false
  // transition with the failure still held, and query-core gates the tick on
  // `focusManager.isFocused()` — so a transition-keyed restore moved focus on
  // its own every 30 seconds for the length of an outage, precisely when the
  // window was focused and the theft felt. On a freshly loaded page nobody has
  // clicked, `<body>` is already active, so the guard that protects moved focus
  // cannot see the difference; only the click can say it happened.
  //
  // `useRestoreFocusOnSettle` is that mechanism: armed from the Retry
  // `onClick`, cleared unconditionally on every settle, and declining on a
  // detached node — which is how "the failure cleared" is answered without the
  // focus path reading the held failure at all, since a successful retry
  // unmounts this branch before the effect can focus anything.
  //
  // What it is fed is `retry.pending`, not `findings.isFetching`, and that is
  // the same distinction one layer further in: the poll shares the query's
  // fetching flag, so `isFetching` here restored focus for a read nobody
  // started, and a poll landing on top of the operator's own settle left the
  // keyboard operator on `<body>` for the rest of the outage. One flag drives
  // all three bindings below — `disabled`, the label, and this `pending` — so
  // the disabled window and the restore cannot drift apart.
  const retry = useOperatorRefetch(findings.refetch);
  // Where the operator lands when their retry *works*. The pane holding Retry
  // is gone by then, so this is read at the settle rather than at the press,
  // and it answers for whichever of the three shapes a successful read mounted:
  // the scope control's first button where there is one (`Needs attention
  // <n>`, already named and already first in the restored surface's tab
  // order), and the "Nothing to report" pane itself where there is not.
  //
  // The ref is on the `.wm-scope` group, never inside a scope `<button>`:
  // `ts_theme_check.cjs` spans the whole opening element, so a line touched
  // anywhere in one makes the gate reject the raw element.
  const scopeRef = useRef<HTMLDivElement | null>(null);
  const emptyPaneRef = useRef<HTMLDivElement | null>(null);
  const landing = useCallback(
    () => emptyPaneRef.current ?? scopeRef.current?.querySelector("button") ?? null,
    [],
  );
  const rememberRetry = useRestoreFocusOnSettle(retry.pending, landing);

  const all = useMemo(() => findings.data ?? [], [findings.data]);
  const live = useMemo(() => all.filter(isLiveFinding), [all]);
  const shown = scope === "attention" ? live : all;
  const view = useMemo(() => arrange(shown, ticketId), [shown, ticketId]);

  // Error: named, with the reason, and visibly distinct from "nothing to
  // report" — a monitor that renders an empty list when it could not ask is
  // the exact failure this surface exists to stop.
  //
  // Above the loading branch, and guarded on the held failure rather than
  // `findings.isError`: both halves are the fix. A held failure must win over
  // the pending state the operator's own Retry re-enters, or the pane unmounts
  // into "Loading findings…" and the reason goes with it.
  //
  // `!all.length` is the other half: this pane is for the read that lost
  // nothing. A failed re-read over findings already on screen keeps the list
  // and says so inline below. Not `findings.isSuccess` — a read that succeeded
  // with zero findings and then failed has nothing to lose either, and
  // "Nothing to report" would be a health claim the monitor cannot make.
  if (failure && !all.length) {
    return (
      <Centred role="alert">
        <div className="wm-error-title">Could not load monitor findings</div>
        {/* The reason stands alone: it is the server's own text, and a
            non-JSON proxy page reaches `request()`'s `message = text` fallback
            at thousands of characters. Bounded and left-aligned by
            `.wm-error-reason` so it cannot push Retry off the viewport. */}
        <div
          className="wm-muted wm-narrow wm-error-reason scroll-focus-ring"
          role="group"
          aria-label="Reason the monitor could not be read"
          tabIndex={0}
        >
          {endSentence(describeError(failure.error, "The monitor endpoint did not answer."))}
        </div>
        <div className="wm-muted wm-narrow">
          Nothing is known about the pipeline&rsquo;s health until this succeeds — this is not the same as having
          nothing to report.
        </div>
        <Button
          variant="plain"
          className="tab-btn"
          onClick={(event) => {
            rememberRetry(event.currentTarget);
            retry.start();
          }}
          disabled={retry.pending}
        >
          {retry.pending ? "Retrying…" : "Retry"}
        </Button>
      </Centred>
    );
  }

  // Loading: the endpoint recomputes workspace conditions per request and can
  // take a few seconds — say so, rather than show a blank pane.
  if (findings.isPending) {
    return (
      <Centred>
        <div className="wm-muted">Loading findings… the monitor recomputes workspace checks on each request.</div>
      </Centred>
    );
  }

  if (!all.length) {
    // Inlined rather than a `Centred`, because this pane is the landing for a
    // retry that succeeded with nothing to report: it has no scope control and
    // no findings list, so the container itself is the target. Named, and
    // `tabIndex={-1}` so it is focusable programmatically without acquiring a
    // Tab stop that answers nothing — the pattern the error reason above uses.
    return (
      <div
        className="wm-centred"
        ref={emptyPaneRef}
        role="group"
        aria-label="Monitor findings"
        tabIndex={-1}
      >
        <div className="wm-empty-title">Nothing to report</div>
        <div className="wm-muted wm-narrow">
          The workflow monitor runs on the reconcile timer and records what it notices — a stage attempted far more
          than its baseline, a run still going long past what that stage has ever taken, a ticket cursor pointing at
          a stage its workflow does not have. Findings appear here as they are written.
        </div>
      </div>
    );
  }

  const finished = all.length - live.length;
  const nothingShown = !view.current && !view.others.length && !view.workspace.length;

  return (
    <div className="wm-root">
      <header className="wm-header">
        <div className="wm-scope" ref={scopeRef} role="group" aria-label="Which findings to show">
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

      {/* A failed re-read over data the operator is already reading keeps the
          list and says so here, politely: `queryClient.ts`'s `queryCache.onError`
          suppresses the query toast whenever `data !== undefined`, so this live
          region is the only report of the failed read. Reaching this return
          already means data is present and the last read failed, so `failure`
          alone is the whole condition. The notice itself is `StaleReadNotice`,
          shared with the four other polling surfaces that report a stale read,
          so the five do not invent five looks or five postures. */}
      {failure && (
        <StaleReadNotice
          lead="This list may be out of date — the last refresh failed."
          error={failure.error}
          fallback="The monitor endpoint did not answer."
          testId="wm-refresh-failed"
        />
      )}

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
