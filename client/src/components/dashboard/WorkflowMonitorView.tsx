import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { conditionLabel, groupFindings } from "../../lib/monitorFindings";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";

/**
 * Every current workflow-monitor finding, across every ticket.
 *
 * The gap this closes, measured: the monitor observes well and its output
 * reached nobody. Findings persist as `monitor_finding` artifacts and the only
 * client call passed a ticket id, so a finding was visible solely to someone who
 * had already opened the exact ticket it was about — which is the one thing a
 * person scanning for problems cannot know to do.
 *
 * What it cost: `unbudgeted_repeat` fired twice on blobert, and that condition's
 * own definition names the defect it was seeing ("the standalone dispatch path
 * had no retry budget at all"). The cause was found by hand weeks later, while
 * the symptom was being written to the database on every reconcile tick.
 *
 * Grouped by condition and stage rather than listed flat, because recurrence is
 * the signal. One `stage_thrash` is a bad afternoon; the same condition on the
 * same stage across five tickets is a pipeline fault, and a flat list of five
 * rows reads as five unrelated problems.
 *
 * Report-only, like the monitor itself. There is no resolve, dismiss or fix
 * control here: `MonitorConfig.mode` defaults to REPORT and deciding what a
 * thrashing stage means is the rework ledger's call, not this panel's.
 */

function Centred({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        height: "100%",
        minHeight: 340,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        padding: 24,
        textAlign: "center",
      }}
    >
      {children}
    </div>
  );
}

export function WorkflowMonitorView() {
  const findings = useQuery({
    queryKey: ["monitor-findings", "all"],
    queryFn: () => api.monitorFindings(),
    refetchInterval: 30_000,
  });

  // Loading: a skeleton would claim to know the shape, and the list is usually
  // short or empty. Say what is happening and nothing more.
  if (findings.isPending) {
    return (
      <Centred>
        <div style={{ fontSize: 12.5, color: "var(--txl)" }}>Loading findings…</div>
      </Centred>
    );
  }

  // Error: named, with the reason, and visibly distinct from "nothing to
  // report" — a monitor panel that renders an empty list when it could not ask
  // is the exact failure this whole surface exists to stop.
  if (findings.isError) {
    return (
      <Centred>
        <div style={{ fontFamily: "var(--dp)", fontSize: 14, color: "var(--rdl)" }}>
          Could not load monitor findings
        </div>
        <div style={{ fontSize: 12.5, color: "var(--txm)", maxWidth: 360, lineHeight: 1.55 }}>
          {describeError(findings.error, "The monitor endpoint did not answer.")} Nothing is
          known about the pipeline's health until this succeeds — this is not the same as
          having nothing to report.
        </div>
        <button
          type="button"
          className="tab-btn"
          onClick={() => findings.refetch()}
          disabled={findings.isFetching}
        >
          {findings.isFetching ? "Retrying…" : "Retry"}
        </button>
      </Centred>
    );
  }

  const groups = groupFindings(findings.data ?? []);

  // Empty: says what would be here and how one appears, so it cannot be read as
  // the panel being broken.
  if (!groups.length) {
    return (
      <Centred>
        <div style={{ fontFamily: "var(--dp)", fontSize: 14, color: "var(--txm)" }}>
          Nothing to report
        </div>
        <div style={{ fontSize: 12.5, color: "var(--txl)", maxWidth: 360, lineHeight: 1.55 }}>
          The workflow monitor runs on the reconcile timer and records what it notices — a
          stage attempted far more than its baseline, a run still going long past what that
          stage has ever taken, a ticket cursor pointing at a stage its workflow does not
          have. Findings appear here as they are written.
        </div>
      </Centred>
    );
  }

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8, minHeight: 0 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div style={{ fontSize: 12.5, color: "var(--txm)" }}>
          {groups.length} {groups.length === 1 ? "condition" : "conditions"} across{" "}
          {findings.data?.length ?? 0} {findings.data?.length === 1 ? "finding" : "findings"}
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
      </div>

      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
        {groups.map((group) => (
          <li key={group.key} className="list-btn" style={{ padding: "11px 16px" }}>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                gap: 8,
              }}
            >
              <div style={{ fontFamily: "var(--dp)", fontSize: 13, color: "var(--tx)" }}>
                {conditionLabel(group.condition)}
                {group.stageKey && (
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)" }}>
                    {" "}
                    · {group.stageKey}
                  </span>
                )}
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txl)" }}>
                {group.tickets > 0 &&
                  `${group.tickets} ${group.tickets === 1 ? "ticket" : "tickets"}`}
                {/*
                  Duration, never `occurrences` — that field counts sweep ticks
                  and reads 5989 against the live database. See groupFindings.
                */}
                {group.tickets > 0 && group.firstSeen && " · "}
                {group.firstSeen && `first seen ${formatRelativeAge(group.firstSeen)}`}
              </div>
            </div>
            <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "grid", gap: 4 }}>
              {group.findings.map((finding, index) => (
                <li
                  key={`${finding.ticket_id}:${index}`}
                  style={{ fontSize: 12, lineHeight: 1.55, color: "var(--txm)" }}
                >
                  {finding.summary}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  );
}
