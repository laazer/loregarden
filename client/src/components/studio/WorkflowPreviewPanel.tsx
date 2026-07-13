import type { StudioWorkflowStage } from "../../api/client";

function stageTypeClass(type: StudioWorkflowStage["stage_type"]): string {
  if (type === "classify") return "classify";
  if (type === "gate") return "gate";
  if (type === "parallel") return "parallel";
  return "agent";
}

function stageTypeLabel(type: StudioWorkflowStage["stage_type"]): string {
  if (type === "classify") return "Classify";
  if (type === "gate") return "Gate";
  if (type === "parallel") return "Parallel";
  return "Agent";
}

export function WorkflowPreviewPanel({
  name,
  slug,
  stages,
  agentLabel,
}: {
  name: string;
  slug: string;
  stages: StudioWorkflowStage[];
  agentLabel: (agentId: string) => string;
}) {
  const displayName = name.trim() || "Untitled workflow";
  const displaySlug = slug.trim() || "workflow-slug";

  return (
    <aside className="studio-preview studio-preview--workflow">
      <div className="studio-preview-live">
        <span className="studio-preview-live-dot" aria-hidden />
        <span className="studio-preview-live-label">Pipeline preview</span>
      </div>
      <div style={{ margin: "6px 0 18px" }}>
        <div className="studio-pipeline-title">{displayName}</div>
        <div className="studio-pipeline-meta">
          {displaySlug} · {stages.length} stage{stages.length === 1 ? "" : "s"}
        </div>
      </div>
      {stages.length === 0 ? (
        <p className="studio-preview-hint">Add stages to see the pipeline.</p>
      ) : (
        stages.map((stage, index) => {
          const typeClass = stageTypeClass(stage.stage_type);
          return (
            <div key={`${stage.key}-${index}`} className="studio-pipeline-stage">
              <div className="studio-pipeline-spine">
                <div className={`studio-pipeline-node ${typeClass}`}>
                  <span className="studio-pipeline-node-dot" aria-hidden />
                </div>
              </div>
              <div className="studio-pipeline-content">
                <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 3 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--tx)" }}>
                    {stage.name || `Stage ${index + 1}`}
                  </span>
                  <span className={`studio-stage-type-badge ${typeClass}`}>{stageTypeLabel(stage.stage_type)}</span>
                </div>
                {stage.stage_type === "parallel" ? (
                  <div className="studio-pipeline-parallel-agents">
                    {stage.parallel_agents.length === 0 ? (
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txl)" }}>
                        No agents added yet
                      </span>
                    ) : (
                      stage.parallel_agents.map((member, memberIndex) => (
                        <span key={`${member.agent_id}-${memberIndex}`} className="studio-pipeline-parallel-chip">
                          {agentLabel(member.agent_id)}
                          {member.skill_name ? <span style={{ color: "var(--txl)" }}> · {member.skill_name}</span> : null}
                        </span>
                      ))
                    )}
                  </div>
                ) : (
                  <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txm)" }}>
                    {agentLabel(stage.agent_id)}
                    <span style={{ color: "var(--txl)" }}> · {stage.skill_name || "—"}</span>
                  </div>
                )}
                {stage.gate_required && (
                  <div
                    style={{
                      marginTop: 6,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 5,
                      fontSize: 10,
                      fontWeight: 600,
                      color: "var(--rdl)",
                      background: "rgba(255,106,84,.1)",
                      border: "1px solid rgba(255,106,84,.25)",
                      borderRadius: 6,
                      padding: "2px 7px",
                    }}
                  >
                    gate · human approval
                  </div>
                )}
              </div>
            </div>
          );
        })
      )}
    </aside>
  );
}
