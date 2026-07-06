import type { StudioGateCheck, StudioHandoffCheck } from "../../api/client";

export function GateHandoffEditor({
  gateChecks,
  handoffChecks,
  onChange,
}: {
  gateChecks: StudioGateCheck[];
  handoffChecks: StudioHandoffCheck[];
  onChange: (gates: StudioGateCheck[], handoffs: StudioHandoffCheck[]) => void;
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
      <section>
        <div className="modal-section-title">Gate checks</div>
        <p className="modal-hint">Human approval gates before the next stage proceeds.</p>
        {gateChecks.map((gate, index) => (
          <div key={index} className="state-card" style={{ marginBottom: 8 }}>
            <select
              className="btn-secondary filter-select"
              style={{ width: "100%", marginBottom: 6 }}
              value={gate.kind}
              onChange={(e) => {
                const next = [...gateChecks];
                next[index] = { ...gate, kind: e.target.value };
                onChange(next, handoffChecks);
              }}
            >
              <option value="workflow_gate">Workflow gate</option>
              <option value="ac_review">AC review</option>
              <option value="human_approval">Human approval</option>
            </select>
            <input
              className="btn-secondary"
              style={{ width: "100%", marginBottom: 6, boxSizing: "border-box" }}
              placeholder="Title"
              value={gate.title}
              onChange={(e) => {
                const next = [...gateChecks];
                next[index] = { ...gate, title: e.target.value };
                onChange(next, handoffChecks);
              }}
            />
            <input
              className="btn-secondary"
              style={{ width: "100%", boxSizing: "border-box" }}
              placeholder="Impact / description"
              value={gate.impact}
              onChange={(e) => {
                const next = [...gateChecks];
                next[index] = { ...gate, impact: e.target.value };
                onChange(next, handoffChecks);
              }}
            />
          </div>
        ))}
        <button
          type="button"
          className="btn-secondary btn-compact"
          onClick={() => onChange([...gateChecks, { kind: "workflow_gate", title: "", impact: "" }], handoffChecks)}
        >
          + Add gate check
        </button>
      </section>

      <section>
        <div className="modal-section-title">Handoff checks</div>
        <p className="modal-hint">Instructions the agent must satisfy before completing the stage via MCP.</p>
        {handoffChecks.map((handoff, index) => (
          <div key={index} className="state-card" style={{ marginBottom: 8 }}>
            <select
              className="btn-secondary filter-select"
              style={{ width: "100%", marginBottom: 6 }}
              value={handoff.kind}
              onChange={(e) => {
                const next = [...handoffChecks];
                next[index] = { ...handoff, kind: e.target.value };
                onChange(gateChecks, next);
              }}
            >
              <option value="mcp_complete">MCP complete_stage</option>
              <option value="blocking_clear">Clear blocking issues</option>
              <option value="custom">Custom check</option>
            </select>
            <textarea
              className="btn-secondary"
              style={{ width: "100%", minHeight: 72, boxSizing: "border-box" }}
              placeholder="What the agent must verify or do before handoff"
              value={handoff.prompt}
              onChange={(e) => {
                const next = [...handoffChecks];
                next[index] = { ...handoff, prompt: e.target.value };
                onChange(gateChecks, next);
              }}
            />
          </div>
        ))}
        <button
          type="button"
          className="btn-secondary btn-compact"
          onClick={() =>
            onChange(gateChecks, [
              ...handoffChecks,
              { kind: "mcp_complete", prompt: "Call loregarden_complete_stage when deliverables are ready." },
            ])
          }
        >
          + Add handoff check
        </button>
      </section>
    </div>
  );
}
