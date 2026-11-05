/**
 * An agent page's first tab: where and why it fails, the runs behind that, and
 * what it is configured to do. The index ranks agents by failure rate; this is
 * the answer to the question that ranking raises, one click on.
 */

import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import type { AgentRunRecord, StudioAgent } from "../../api/types";
import { ratePercent } from "../../lib/agentIndex";
import { failureKindLabel } from "../../lib/agentRunRecord";
import { ticketPath } from "../../lib/appNavigation";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { AgentVersionHistory } from "./AgentVersionHistory";
import { ADAPTERS } from "./studioAgentMeta";
import "./AgentOverview.css";

export type AgentTab = "overview" | "instructions" | "runtime" | "prompt";

interface Props {
  agent: StudioAgent;
  record: UseQueryResult<AgentRunRecord>;
  onEdit: (tab: AgentTab) => void;
}

function WhereItFails({ record }: { record: AgentRunRecord }) {
  const [showAll, setShowAll] = useState(false);
  if (!record.stages.length) {
    return (
      <p className="studio-record-muted">
        No finished runs yet. A stage that names this agent runs it; its record starts with the first one.
      </p>
    );
  }
  // Stages it never failed at answer nothing here; they fold under one line.
  const failing = record.stages.filter((stage) => stage.failed > 0);
  const clean = record.stages.length - failing.length;
  const shown = showAll || !failing.length ? record.stages : failing;
  return (
    <>
      <table className="agent-record-table">
        <thead>
          <tr>
            <th scope="col">Stage</th>
            <th scope="col" className="num">
              Runs
            </th>
            <th scope="col" className="num">
              Failed
            </th>
            <th scope="col">Why</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((stage) => (
            <tr key={stage.stage_key || "(none)"}>
              <th scope="row" className="agent-record-stage">
                {stage.stage_key || <span className="studio-record-muted">no stage</span>}
              </th>
              <td className="num">{stage.runs.toLocaleString()}</td>
              <td className={`num${stage.failed ? " agent-record-failed" : ""}`}>
                {stage.failed ? `${stage.failed.toLocaleString()} · ${ratePercent(stage.failed / stage.runs)}%` : "0"}
              </td>
              <td className="agent-record-why">
                {stage.failed_by_kind.length
                  ? stage.failed_by_kind
                      .map((entry) => `${entry.count.toLocaleString()} ${failureKindLabel(entry.kind)}`)
                      .join(" · ")
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {clean && failing.length ? (
        <Button
          variant="plain"
          className="agent-record-more"
          aria-expanded={showAll}
          onClick={() => setShowAll((value) => !value)}
        >
          {showAll ? "Hide stages with no failures" : `${clean} more stage${clean === 1 ? "" : "s"} with no failures`}
        </Button>
      ) : null}
    </>
  );
}

function RecentRuns({ record }: { record: AgentRunRecord }) {
  if (!record.recent.length) return <p className="studio-record-muted">No runs yet.</p>;
  return (
    <ul className="agent-record-runs">
      {record.recent.map((run) => {
        const failed = run.status === "failed";
        const label = run.ticket_external_id ?? run.run_code;
        return (
          <li key={run.id} className="agent-record-run">
            <span
              className={`agent-record-run-mark is-${run.status}`}
              role="img"
              aria-label={run.status.replace("_", " ")}
            />
            <span className="agent-record-run-main">
              {run.ticket_external_id ? (
                <Link className="agent-record-run-link" to={ticketPath(run.ticket_external_id)}>
                  {run.ticket_title || label}
                </Link>
              ) : (
                <span className="agent-record-run-code">{label}</span>
              )}
              <span className="agent-record-run-meta">
                {run.ticket_external_id ? `${run.ticket_external_id} · ` : ""}
                {run.stage_key || "no stage"}
                {failed ? ` · ${failureKindLabel(run.failure_kind)}` : ""}
              </span>
            </span>
            <span className="agent-record-run-age">{formatRelativeAge(run.created_at)}</span>
          </li>
        );
      })}
    </ul>
  );
}

function Configuration({ agent, onEdit }: { agent: StudioAgent; onEdit: Props["onEdit"] }) {
  const provider = ADAPTERS.find((option) => option.id === agent.adapter)?.label ?? agent.adapter;
  return (
    <>
      <dl className="studio-record-dl">
        <dt>Provider</dt>
        <dd>{provider || "—"}</dd>
        <dt>Model</dt>
        <dd>{agent.default_model || "provider default"}</dd>
        <dt>Skill</dt>
        <dd>{agent.default_skill || "—"}</dd>
        <dt>Timeout</dt>
        <dd>{agent.timeout.toLocaleString()} s</dd>
        <dt>MCP tools</dt>
        <dd>{agent.mcp_enabled ? `${agent.mcp_tools.length} on` : "off"}</dd>
        <dt>Checks</dt>
        <dd>
          {agent.gate_checks.length} gate · {agent.handoff_checks.length} hand-off
        </dd>
        {agent.role_file ? (
          <>
            <dt>Role file</dt>
            <dd className="agent-record-mono">{agent.role_file}</dd>
          </>
        ) : null}
      </dl>
      <div className="agent-record-edit-links">
        <Button variant="secondary" compact onClick={() => onEdit("instructions")}>
          Edit instructions
        </Button>
        <Button variant="secondary" compact onClick={() => onEdit("runtime")}>
          Edit runtime & tools
        </Button>
      </div>
    </>
  );
}

export function AgentOverview({ agent, record, onEdit }: Props) {
  return (
    <>
      {agent.description ? <p className="studio-record-description">{agent.description}</p> : null}
      <div className="studio-record-columns">
        <div className="studio-record-section-stack">
          <section className="studio-record-section" aria-labelledby="agent-where-fails">
            <h3 id="agent-where-fails" className="studio-record-section-title">
              Where it fails
            </h3>
            {record.isPending ? (
              <PaneSkeleton variant="list" rows={4} label="Loading this agent's runs…" />
            ) : record.isError ? (
              <div className="agent-record-error" role="alert">
                <span>Could not load this agent's runs: {describeError(record.error, "the request failed")}</span>
                <Button variant="secondary" compact onClick={() => void record.refetch()}>
                  Try again
                </Button>
              </div>
            ) : (
              <WhereItFails record={record.data} />
            )}
          </section>
          {record.data ? (
            <section className="studio-record-section" aria-labelledby="agent-recent-runs">
              <h3 id="agent-recent-runs" className="studio-record-section-title">
                Recent runs
              </h3>
              <RecentRuns record={record.data} />
            </section>
          ) : null}
        </div>
        <div className="studio-record-section-stack">
          <section className="studio-record-section" aria-labelledby="agent-configuration">
            <h3 id="agent-configuration" className="studio-record-section-title">
              Configuration
            </h3>
            <Configuration agent={agent} onEdit={onEdit} />
          </section>
          <AgentVersionHistory slug={agent.slug} currentVersion={agent.version} />
        </div>
      </div>
    </>
  );
}
