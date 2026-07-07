import { useMemo } from "react";

import type { TicketDetail } from "../../api/client";
import { buildHiveSimulation } from "../../lib/hiveSimulation";
import "./HiveSimulationPanel.css";

function OrchestratorIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#04140f" strokeWidth="2.2">
      <path d="M12 3v18M5 8l7-5 7 5M5 16l7 5 7-5" />
    </svg>
  );
}

function OfficeIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="1.9">
      <path d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6" />
    </svg>
  );
}

export function HiveSimulationPanel({ ticket }: { ticket: TicketDetail }) {
  const model = useMemo(() => buildHiveSimulation(ticket.stages ?? []), [ticket.stages]);

  return (
    <div className="hive-panel">
      <div className="hive-panel__header">
        <OfficeIcon />
        <span className="hive-panel__title">Office floor</span>
        <span className="hive-panel__live">
          <span className="hive-panel__live-dot" aria-hidden />
          live
        </span>
        <div className="hive-panel__spacer" />
        <span className="hive-panel__legend">
          <span className="hive-panel__legend-dot hive-panel__legend-dot--working" aria-hidden />
          working
        </span>
        <span className="hive-panel__legend">
          <span className="hive-panel__legend-dot hive-panel__legend-dot--done" aria-hidden />
          done
        </span>
        <span className="hive-panel__legend">
          <span className="hive-panel__legend-dot hive-panel__legend-dot--idle" aria-hidden />
          idle
        </span>
      </div>

      <div className="hive-panel__floor">
        <svg className="hive-panel__lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
          {model.lines.map((line, index) => (
            <line
              key={`${line.x2}-${line.y2}-${index}`}
              className={line.animated ? "hive-panel__line hive-panel__line--animated" : "hive-panel__line"}
              x1={line.x1}
              y1={line.y1}
              x2={line.x2}
              y2={line.y2}
              stroke={line.color}
              strokeWidth={line.width}
              opacity={line.opacity}
              strokeDasharray={line.dashed ? "3 3" : undefined}
            />
          ))}
        </svg>

        <div className="hive-panel__flyer hive-panel__flyer--a" aria-hidden />
        <div className="hive-panel__flyer hive-panel__flyer--b" aria-hidden />
        <div className="hive-panel__flyer hive-panel__flyer--c" aria-hidden />

        <div className="hive-panel__orchestrator">
          <div
            className={`hive-panel__orchestrator-icon${
              model.orchestratorActive ? " hive-panel__orchestrator-icon--active" : ""
            }`}
          >
            <OrchestratorIcon />
          </div>
          <div className="hive-panel__orchestrator-copy">
            <div className="hive-panel__orchestrator-name">Orchestrator</div>
            <div className="hive-panel__orchestrator-sub">routes · gates · handoffs</div>
          </div>
        </div>

        {model.agents.map((agent) => (
          <div
            key={agent.id}
            className="hive-panel__desk"
            style={{ left: agent.x, top: agent.y }}
          >
            <div
              className={`hive-panel__avatar${agent.pulsing ? " hive-panel__avatar--pulse" : ""}`}
              style={{
                background: agent.avBg,
                borderColor: agent.ring,
                color: agent.avFg,
              }}
            >
              {agent.init}
              <span className="hive-panel__avatar-dot" style={{ background: agent.color }} aria-hidden />
            </div>
            <div className="hive-panel__desk-card" style={{ background: agent.deskBg }}>
              <div className="hive-panel__desk-name">{agent.name}</div>
              <div className="hive-panel__desk-status" style={{ color: agent.color }}>
                <span className="hive-panel__desk-status-dot" style={{ background: agent.color }} aria-hidden />
                {agent.status} · {agent.stage}
              </div>
              {agent.showTool ? (
                <div className="hive-panel__desk-skill">▸ {agent.skill}</div>
              ) : null}
            </div>
          </div>
        ))}

        {model.idle ? (
          <div className="hive-panel__idle">
            <div className="hive-panel__idle-title">The floor is quiet</div>
            <div className="hive-panel__idle-copy">
              No agents assigned yet — advance this ticket to staff the workflow.
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
