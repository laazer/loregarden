import type { ReactNode } from "react";
import type { TicketDetail } from "../../api/client";
import type { ContextSection } from "../../api/types";
import { formatLocalTimestamp, formatRelativeAge, runTimestamp } from "../../lib/timestamps";
import { InlineCodeDiffReview } from "../InlineCodeDiffReview";
import { RunLedgerPanel } from "../RunLedgerPanel";

export function ArtifactView({
  tab,
  ticket,
  runs = [],
  onOpenEditorFile,
  onOpenPr,
  isOpeningPr = false,
  onCommitPush,
  isCommittingPush = false,
  onOpenRunLog,
}: {
  tab: string;
  ticket?: TicketDetail;
  runs?: {
    id: string;
    run_code: string;
    status: string;
    command: string;
    agent_id?: string;
    stage_key?: string;
    created_at?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    stderr?: string;
    stdout?: string;
  }[];
  onOpenEditorFile?: (filePath: string) => void;
  onOpenPr?: () => void;
  isOpeningPr?: boolean;
  onCommitPush?: () => void;
  isCommittingPush?: boolean;
  onOpenRunLog?: (runId: string) => void;
}) {
  if (!ticket) {
    return <div style={{ padding: 40, color: "var(--txl)", textAlign: "center" }}>No ticket selected</div>;
  }
  const art = ticket.artifacts ?? {};

  if (tab === "ledger") {
    return (
      <RunLedgerPanel
        ticketId={ticket.id}
        isActive={runs.some((r) => r.status === "running" || r.status === "awaiting_permission")}
        onOpenRunLog={onOpenRunLog}
      />
    );
  }

  if (tab === "diff") {
    const diff = art.diff;
    if (!diff) {
      return (
        <EmptyArtifacts label="No diff captured yet">
          Shows git changes in the workspace repo (vs main) after agent runs, or when you open this tab.
        </EmptyArtifacts>
      );
    }
    return (
      <InlineCodeDiffReview
        ticketId={ticket.id}
        diff={diff}
        diffSummary={{
          files: diff.files,
          range: diff.range,
          add: diff.add,
          del: diff.del,
        }}
        onOpenEditorFile={onOpenEditorFile}
        onCommitPush={onCommitPush}
        isCommittingPush={isCommittingPush}
      />
    );
  }

  if (tab === "errors") {
    const errorArt = art.error;
    const failedRuns = runs.filter((r) => r.status === "failed");
    const hasContent = Boolean(ticket.blocking_issues || errorArt || failedRuns.length);
    if (!hasContent) return <EmptyArtifacts label="No errors recorded" />;
    // The error artifact records a run_code, not an id — the log fetch needs an
    // id, and the run row is also where that failure's timestamps live.
    const errorRun = errorArt ? runs.find((r) => r.run_code === errorArt.run_code) : undefined;
    const errorRunId = errorRun?.id;

    return (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 6, minHeight: 0 }}>
        {(ticket.blocking_issues || errorArt?.message) && (
          <div
            className="list-btn"
            style={{
              padding: "11px 16px",
              borderColor: "rgba(240,96,63,.35)",
              background: "rgba(240,96,63,.08)",
            }}
          >
            <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--rdl)" }}>
              Blocking issue
            </div>
            <pre
              style={{
                margin: "8px 0 0",
                fontFamily: "var(--mono)",
                fontSize: 12,
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
                color: "var(--tx)",
              }}
            >
              {errorArt?.message || ticket.blocking_issues}
            </pre>
          </div>
        )}
        {errorArt && (
          <div className="list-btn" style={{ padding: "11px 16px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--txl)" }}>
                Failed run
              </div>
              {onOpenRunLog && errorRunId && (
                <ViewLogButton onClick={() => onOpenRunLog(errorRunId)} />
              )}
            </div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
              {errorArt.run_code} · {errorArt.agent_id} · {errorArt.stage_key}
            </div>
            {errorRun && <RunWhen run={errorRun} />}
            {errorArt.command && (
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--txl)", marginTop: 8, wordBreak: "break-all" }}>
                {errorArt.command}
              </div>
            )}
          </div>
        )}
        {failedRuns.map((run) => (
          <div key={run.id} className="list-btn" style={{ padding: "11px 16px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--tx)" }}>{run.run_code}</div>
              {onOpenRunLog && <ViewLogButton onClick={() => onOpenRunLog(run.id)} />}
            </div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
              {run.agent_id ?? "—"} · {run.stage_key ?? "—"}
            </div>
            <RunWhen run={run} />
            {run.stderr && (
              <pre
                style={{
                  margin: "8px 0 0",
                  fontFamily: "var(--mono)",
                  fontSize: 11,
                  lineHeight: 1.55,
                  whiteSpace: "pre-wrap",
                  color: "var(--rdl)",
                }}
              >
                {run.stderr}
              </pre>
            )}
          </div>
        ))}
      </div>
    );
  }

  if (tab === "tests") {
    const tests = art.tests;
    if (!tests) {
      return (
        <EmptyArtifacts label="No test results yet">
          Populated when a testing stage run completes (static_qa, test_breaker, test_designer) with pytest-style output.
        </EmptyArtifacts>
      );
    }
    return (
      <div style={{ padding: 16 }}>
        <div className="state-card" style={{ marginBottom: 14 }}>
          <strong>{tests.summary}</strong>
          {tests.cmd && (
            <div
              style={{
                marginTop: 8,
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--txl)",
                wordBreak: "break-all",
                lineHeight: 1.5,
              }}
            >
              {tests.cmd}
            </div>
          )}
        </div>
        {tests.rows?.map((row, i) => (
          <div key={i} className="list-btn" style={{ marginBottom: 4 }}>
            <span style={{ color: row.status === "pass" ? "var(--grl)" : "var(--rdl)" }}>{row.status}</span>{" "}
            <span style={{ fontFamily: "var(--mono)" }}>{row.name}</span>
            {row.msg && <div style={{ color: "var(--rdl)", fontSize: 11, marginTop: 4 }}>{row.msg}</div>}
          </div>
        ))}
      </div>
    );
  }

  if (tab === "pr") {
    const pr = art.pr;
    if (!pr) {
      return (
        <EmptyArtifacts label="No pull request opened">
          Open a PR from the approval step when human sign-off is required.
          {(onCommitPush || onOpenPr) && (
            <div style={{ marginTop: 16, display: "flex", gap: 8, justifyContent: "center" }}>
              {onCommitPush && (
                <button type="button" className="btn-secondary" disabled={isCommittingPush} onClick={onCommitPush}>
                  {isCommittingPush ? "Committing…" : "Commit & push"}
                </button>
              )}
              {onOpenPr && (
                <button type="button" className="btn-secondary" disabled={isOpeningPr} onClick={onOpenPr}>
                  {isOpeningPr ? "Opening PR…" : "Open PR"}
                </button>
              )}
            </div>
          )}
        </EmptyArtifacts>
      );
    }
    return (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="state-card">
          <div className="state-label">Pull request</div>
          <div style={{ fontWeight: 600, marginTop: 8 }}>{pr.title}</div>
          {pr.number && (
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
              #{pr.number} · {pr.branch}
            </div>
          )}
          <a
            href={pr.url}
            target="_blank"
            rel="noreferrer"
            style={{ display: "inline-block", marginTop: 12, color: "var(--ac2)", fontSize: 13 }}
          >
            {pr.url}
          </a>
        </div>
        {pr.body && (
          <pre
            style={{
              margin: 0,
              padding: 12,
              borderRadius: 10,
              border: "1px solid var(--bd)",
              background: "var(--bg2)",
              fontFamily: "var(--mono)",
              fontSize: 11,
              lineHeight: 1.55,
              whiteSpace: "pre-wrap",
            }}
          >
            {pr.body}
          </pre>
        )}
      </div>
    );
  }

  if (tab !== "context") {
    return <EmptyArtifacts />;
  }

  const sections = art.context ?? [];
  const stages = ticket.stages ?? [];
  const runRows = runs;
  const reportsByStage = new Map<string, ContextSection[]>();
  const otherSections: ContextSection[] = [];
  for (const sec of sections) {
    // A stage_key alone does not make a section a report — only a status does.
    if (sec.stage_key && sec.status) {
      const list = reportsByStage.get(sec.stage_key) ?? [];
      list.push(sec);
      reportsByStage.set(sec.stage_key, list);
    } else if (sec.rows?.length) {
      otherSections.push(sec);
    }
  }
  if (!sections.length && !runRows.length && !stages.length) return <EmptyArtifacts />;
  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
      {runRows.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontFamily: "var(--dp)", fontSize: 13, color: "var(--txm)" }}>
            {runRows.length} agent run{runRows.length === 1 ? "" : "s"}
          </div>
          {runRows.map((r) => (
            <button
              key={r.id}
              type="button"
              className="list-btn"
              disabled={!onOpenRunLog}
              onClick={() => onOpenRunLog?.(r.id)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "11px 16px",
                fontFamily: "var(--mono)",
                fontSize: 11,
                cursor: onOpenRunLog ? "pointer" : "default",
              }}
            >
              {r.run_code} · {r.status} · {r.command.slice(0, 60)}
              <RunWhen run={r} />
            </button>
          ))}
        </div>
      )}
      {stages.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontFamily: "var(--dp)", fontSize: 13, color: "var(--txm)" }}>Workflow steps</div>
          {stages.map((stage) => {
            const reports = reportsByStage.get(stage.key) ?? [];
            return (
              <div key={stage.key} className="list-btn" style={{ padding: "11px 16px" }}>
                <div style={{ color: "var(--tx)", fontSize: 13 }}>{stage.name}</div>
                <div style={{ fontSize: 12, color: "var(--txm)", marginTop: 4 }}>
                  Agent: {stage.agent_id || "N/A"} · Status: {stage.status}
                </div>
                {reports.map((report, i) => (
                  <div
                    key={i}
                    style={{
                      marginTop: 8,
                      paddingLeft: 10,
                      borderLeft: "2px solid var(--bd)",
                      fontSize: 12,
                    }}
                  >
                    <div>
                      Report status: <strong>{report.status}</strong>
                      {typeof report.confidence === "number" && (
                        <> · confidence: {report.confidence.toFixed(2)}</>
                      )}
                    </div>
                    {report.reroute_to_stage && (
                      <div style={{ color: "var(--txm)" }}>Reroute to: {report.reroute_to_stage}</div>
                    )}
                    {report.reroute_context && (
                      <div style={{ color: "var(--txm)", fontFamily: "var(--mono)" }}>
                        {report.reroute_context}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
      {otherSections.map((sec, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontFamily: "var(--dp)", fontSize: 13, color: "var(--txm)" }}>{sec.title}</div>
          {sec.rows?.map((r, j) => (
            <div
              key={j}
              className="list-btn"
              style={{ display: "flex", gap: 12, padding: "11px 16px", alignItems: "baseline" }}
            >
              <span style={{ width: 130, flex: "0 0 auto", color: "var(--txm)", fontSize: 12 }}>{r.k}</span>
              <span style={{ fontFamily: "var(--mono)", flex: 1, minWidth: 0, fontSize: 12 }}>{r.v}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * When a run happened, in the viewer's timezone — the answer to "which of these
 * three identical-looking failures am I looking at?". Absolute local time carries
 * the zone name so it can be compared against a provider's reset window; the age
 * beside it saves the arithmetic.
 */
function RunWhen({
  run,
}: {
  run: { created_at?: string | null; started_at?: string | null; finished_at?: string | null };
}) {
  const when = runTimestamp(run);
  if (!when) return null;
  const age = formatRelativeAge(when);
  return (
    <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txl)", marginTop: 4 }}>
      {formatLocalTimestamp(when)}
      {age && ` · ${age}`}
    </div>
  );
}

function ViewLogButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="btn-secondary" style={{ fontSize: 11 }} onClick={onClick}>
      View log
    </button>
  );
}

function EmptyArtifacts({
  label = "No artifacts yet",
  children,
}: {
  label?: string;
  children?: ReactNode;
}) {
  return (
    <div style={{ height: "100%", minHeight: 340, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--txl)", gap: 12, padding: 24 }}>
      <div style={{ fontFamily: "var(--dp)", fontSize: 14, color: "var(--txm)" }}>{label}</div>
      {children ? (
        <div style={{ fontSize: 12.5, textAlign: "center", maxWidth: 320, lineHeight: 1.55 }}>{children}</div>
      ) : (
        label === "No artifacts yet" && (
          <div style={{ fontSize: 12.5, textAlign: "center", maxWidth: 280 }}>Artifacts appear when agent runs complete</div>
        )
      )}
    </div>
  );
}
