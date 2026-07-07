import type { ReactNode } from "react";
import type { TicketDetail } from "../../api/client";
import { InlineCodeDiffReview } from "../InlineCodeDiffReview";

export function ArtifactView({
  tab,
  ticket,
  runs = [],
  onOpenEditorFile,
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
    stderr?: string;
    stdout?: string;
  }[];
  onOpenEditorFile?: (filePath: string) => void;
}) {
  if (!ticket) {
    return <div style={{ padding: 40, color: "var(--txl)", textAlign: "center" }}>No ticket selected</div>;
  }
  const art = ticket.artifacts ?? {};

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
      />
    );
  }

  if (tab === "errors") {
    const errorArt = art.error;
    const failedRuns = runs.filter((r) => r.status === "failed");
    const hasContent = Boolean(ticket.blocking_issues || errorArt || failedRuns.length);
    if (!hasContent) return <EmptyArtifacts label="No errors recorded" />;

    return (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
        {(ticket.blocking_issues || errorArt?.message) && (
          <div
            className="state-card"
            style={{
              borderColor: "rgba(240,96,63,.35)",
              background: "rgba(240,96,63,.08)",
            }}
          >
            <div className="state-label" style={{ color: "var(--rdl)" }}>
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
          <div className="state-card">
            <div className="state-label">Failed run</div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
              {errorArt.run_code} · {errorArt.agent_id} · {errorArt.stage_key}
            </div>
            {errorArt.command && (
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--txl)", marginTop: 8, wordBreak: "break-all" }}>
                {errorArt.command}
              </div>
            )}
          </div>
        )}
        {failedRuns.map((run) => (
          <div key={run.id} className="state-card">
            <div className="state-label">{run.run_code}</div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
              {run.agent_id ?? "—"} · {run.stage_key ?? "—"}
            </div>
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
  const runRows = runs;
  if (!sections.length && !runRows.length) return <EmptyArtifacts />;
  return (
    <div style={{ padding: 16 }}>
      {runRows.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div className="state-label">Agent runs</div>
          {runRows.map((r) => (
            <div key={r.id} className="list-btn" style={{ marginBottom: 4, fontFamily: "var(--mono)", fontSize: 11 }}>
              {r.run_code} · {r.status} · {r.command.slice(0, 60)}
            </div>
          ))}
        </div>
      )}
      {sections.map((sec, i) => (
        <div key={i} style={{ marginBottom: 16 }}>
          <div className="state-label">{sec.title}</div>
          {sec.rows?.map((r, j) => (
            <div key={j} style={{ display: "flex", gap: 12, padding: "9px 0", borderBottom: "1px solid var(--bd)" }}>
              <span style={{ width: 130, color: "var(--txm)" }}>{r.k}</span>
              <span style={{ fontFamily: "var(--mono)", flex: 1 }}>{r.v}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
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
