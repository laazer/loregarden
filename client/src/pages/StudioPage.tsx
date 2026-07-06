import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  api,
  type ClassifyRoute,
  type StudioAgent,
  type StudioGateCheck,
  type StudioHandoffCheck,
  type StudioWorkflow,
  type StudioWorkflowStage,
} from "../api/client";
import { BrandMark } from "../components/BrandMark";
import { AgentPreviewPanel } from "../components/studio/AgentPreviewPanel";
import { McpToolGuideSection } from "../components/studio/McpToolGuideSection";
import { GateHandoffEditor } from "../components/studio/GateHandoffEditor";
import { useUiStore } from "../state/uiStore";

const ADAPTERS = [
  { id: "claude", label: "Claude Code" },
  { id: "cursor", label: "Cursor Agent" },
  { id: "lmstudio", label: "LM Studio" },
  { id: "local", label: "Local runner" },
];

const LANGUAGE_OPTIONS = ["python", "typescript", "javascript", "go", "rust", "java", "sql", "markdown"];
const SPECIALTY_OPTIONS = ["backend", "frontend", "testing", "planning", "research", "devops", "review"];

const EMPTY_AGENT = {
  slug: "",
  name: "",
  description: "",
  role_body: "",
  adapter: "claude",
  timeout: 600,
  default_skill: "",
  mcp_enabled: true,
  mcp_tools: [] as string[],
  gate_checks: [] as StudioGateCheck[],
  handoff_checks: [] as StudioHandoffCheck[],
};

function emptyStage(order: number): StudioWorkflowStage {
  return {
    key: `stage_${order}`,
    name: `Stage ${order}`,
    stage_type: "agent",
    agent_id: "planner",
    skill_name: "plan",
    optional: false,
    order,
    gate_required: false,
    classify_routes: [],
  };
}

export function StudioPage() {
  const qc = useQueryClient();
  const setAppPage = useUiStore((s) => s.setAppPage);
  const [tab, setTab] = useState<"agents" | "workflows">("agents");
  const [selectedAgentSlug, setSelectedAgentSlug] = useState<string | null>(null);
  const [selectedWorkflowSlug, setSelectedWorkflowSlug] = useState<string | null>(null);
  const [agentDraft, setAgentDraft] = useState({ ...EMPTY_AGENT });
  const [workflowDraft, setWorkflowDraft] = useState<{
    slug: string;
    name: string;
    description: string;
    stages: StudioWorkflowStage[];
  }>({ slug: "", name: "", description: "", stages: [emptyStage(1)] });

  const mcpGuides = useQuery({ queryKey: ["studio-mcp-tool-guides"], queryFn: api.studioMcpToolGuides });
  const studioDefaults = useQuery({ queryKey: ["studio-defaults"], queryFn: api.studioDefaults });
  const agents = useQuery({ queryKey: ["studio-agents"], queryFn: api.studioAgents });
  const workflows = useQuery({ queryKey: ["studio-workflows"], queryFn: api.studioWorkflows });
  const skills = useQuery({ queryKey: ["agent-skills"], queryFn: api.skills });

  const customAgents = useMemo(
    () => (agents.data ?? []).filter((agent) => !agent.built_in),
    [agents.data],
  );
  const builtinAgents = useMemo(
    () => (agents.data ?? []).filter((agent) => agent.built_in),
    [agents.data],
  );
  const customWorkflows = useMemo(
    () => (workflows.data ?? []).filter((workflow) => !workflow.built_in && !workflow.read_only),
    [workflows.data],
  );
  const builtinWorkflows = useMemo(
    () => (workflows.data ?? []).filter((workflow) => workflow.built_in || workflow.read_only),
    [workflows.data],
  );

  const selectedAgent = useMemo(
    () => agents.data?.find((item) => item.slug === selectedAgentSlug) ?? null,
    [agents.data, selectedAgentSlug],
  );
  const selectedWorkflow = useMemo(
    () => workflows.data?.find((item) => item.slug === selectedWorkflowSlug) ?? null,
    [workflows.data, selectedWorkflowSlug],
  );
  const isAgentReadOnly = Boolean(selectedAgent?.read_only || selectedAgent?.built_in);
  const isWorkflowReadOnly = Boolean(selectedWorkflow?.read_only || selectedWorkflow?.built_in);
  const isEditingCustomAgent = Boolean(selectedAgentSlug && !isAgentReadOnly);

  const agentOptions = useMemo(() => (agents.data ?? []).map((a) => ({ id: a.slug, label: a.name })), [agents.data]);

  const [previewPayload, setPreviewPayload] = useState<Partial<StudioAgent> & { name: string } | null>(null);

  useEffect(() => {
    const source = isAgentReadOnly && selectedAgent
      ? {
          name: selectedAgent.name,
          description: selectedAgent.description,
          role_body: selectedAgent.role_body,
          adapter: selectedAgent.adapter,
          timeout: selectedAgent.timeout,
          default_skill: selectedAgent.default_skill,
          mcp_enabled: selectedAgent.mcp_enabled,
          mcp_tools: selectedAgent.mcp_tools,
          gate_checks: selectedAgent.gate_checks,
          handoff_checks: selectedAgent.handoff_checks,
        }
      : {
          name: agentDraft.name || "Preview Agent",
          description: agentDraft.description,
          role_body: agentDraft.role_body,
          adapter: agentDraft.adapter,
          timeout: agentDraft.timeout,
          default_skill: agentDraft.default_skill,
          mcp_enabled: agentDraft.mcp_enabled,
          mcp_tools: agentDraft.mcp_tools,
          gate_checks: agentDraft.gate_checks,
          handoff_checks: agentDraft.handoff_checks,
        };
    const timer = window.setTimeout(() => setPreviewPayload(source), 350);
    return () => window.clearTimeout(timer);
  }, [agentDraft, selectedAgent, isAgentReadOnly]);

  const agentPreview = useQuery({
    queryKey: ["studio-agent-preview", previewPayload],
    queryFn: () => api.previewStudioAgent(previewPayload!),
    enabled: tab === "agents" && Boolean(previewPayload?.name),
  });

  const saveAgent = useMutation({
    mutationFn: async () => {
      const payload = {
        ...agentDraft,
        slug: agentDraft.slug || agentDraft.name,
        mcp_tools: agentDraft.mcp_enabled ? agentDraft.mcp_tools : [],
      };
      if (selectedAgentSlug && !agents.data?.find((a) => a.slug === selectedAgentSlug)?.built_in) {
        return api.updateStudioAgent(selectedAgentSlug, payload);
      }
      return api.createStudioAgent(payload);
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["studio-agents"] });
      setSelectedAgentSlug(saved.slug);
    },
  });

  const deleteAgent = useMutation({
    mutationFn: (slug: string) => api.deleteStudioAgent(slug),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["studio-agents"] });
      setSelectedAgentSlug(null);
      setAgentDraft({ ...EMPTY_AGENT });
    },
  });

  const saveWorkflow = useMutation({
    mutationFn: async () => {
      const payload = {
        ...workflowDraft,
        slug: workflowDraft.slug || workflowDraft.name,
        stages: workflowDraft.stages.map((stage, idx) => ({ ...stage, order: idx + 1 })),
      };
      if (selectedWorkflowSlug && !isWorkflowReadOnly) {
        return api.updateStudioWorkflow(selectedWorkflowSlug, payload);
      }
      return api.createStudioWorkflow(payload);
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["studio-workflows"] });
      qc.invalidateQueries({ queryKey: ["workflow-templates"] });
      setSelectedWorkflowSlug(saved.slug);
    },
  });

  const publishWorkflow = useMutation({
    mutationFn: (slug: string) => api.publishStudioWorkflow(slug),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["studio-workflows"] });
      qc.invalidateQueries({ queryKey: ["workflow-templates"] });
    },
  });

  const deleteWorkflow = useMutation({
    mutationFn: (slug: string) => api.deleteStudioWorkflow(slug),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["studio-workflows"] });
      setSelectedWorkflowSlug(null);
      setWorkflowDraft({ slug: "", name: "", description: "", stages: [emptyStage(1)] });
    },
  });

  useEffect(() => {
    if (!selectedAgentSlug || isAgentReadOnly) return;
    const agent = agents.data?.find((item) => item.slug === selectedAgentSlug);
    if (!agent) return;
    setAgentDraft({
      slug: agent.slug,
      name: agent.name,
      description: agent.description,
      role_body: agent.role_body,
      adapter: agent.adapter,
      timeout: agent.timeout,
      default_skill: agent.default_skill,
      mcp_enabled: agent.mcp_enabled,
      mcp_tools: agent.mcp_tools,
      gate_checks: agent.gate_checks,
      handoff_checks: agent.handoff_checks,
    });
  }, [selectedAgentSlug, agents.data, isAgentReadOnly]);

  useEffect(() => {
    if (!selectedWorkflowSlug) return;
    const workflow = workflows.data?.find((item) => item.slug === selectedWorkflowSlug);
    if (!workflow) return;
    setWorkflowDraft({
      slug: workflow.slug,
      name: workflow.name,
      description: workflow.description,
      stages: workflow.stages.length ? workflow.stages : [emptyStage(1)],
    });
  }, [selectedWorkflowSlug, workflows.data]);

  const startNewAgent = () => {
    setSelectedAgentSlug(null);
    const defaults = studioDefaults.data;
    setAgentDraft({
      ...EMPTY_AGENT,
      mcp_tools: defaults?.mcp_tools ?? [],
      handoff_checks: defaults?.handoff_checks ?? [],
      gate_checks: defaults?.gate_checks ?? [],
    });
  };

  const duplicateAgent = (agent: StudioAgent) => {
    setSelectedAgentSlug(null);
    setAgentDraft({
      slug: `${agent.slug}-copy`,
      name: `${agent.name} (copy)`,
      description: agent.description,
      role_body: agent.role_body,
      adapter: agent.adapter,
      timeout: agent.timeout,
      default_skill: agent.default_skill,
      mcp_enabled: agent.mcp_enabled,
      mcp_tools: agent.mcp_tools,
      gate_checks: agent.gate_checks,
      handoff_checks: agent.handoff_checks,
    });
  };

  const duplicateWorkflow = (workflow: StudioWorkflow) => {
    setSelectedWorkflowSlug(null);
    setWorkflowDraft({
      slug: `${workflow.slug}-copy`,
      name: `${workflow.name} (copy)`,
      description: workflow.description,
      stages: workflow.stages.map((stage, idx) => ({ ...stage, order: idx + 1 })),
    });
  };

  const toggleMcpTool = (tool: string) => {
    setAgentDraft((draft) => ({
      ...draft,
      mcp_tools: draft.mcp_tools.includes(tool)
        ? draft.mcp_tools.filter((item) => item !== tool)
        : [...draft.mcp_tools, tool],
    }));
  };

  const updateStage = (index: number, patch: Partial<StudioWorkflowStage>) => {
    setWorkflowDraft((draft) => ({
      ...draft,
      stages: draft.stages.map((stage, idx) => (idx === index ? { ...stage, ...patch } : stage)),
    }));
  };

  const updateRoute = (stageIndex: number, routeIndex: number, patch: Partial<ClassifyRoute>) => {
    setWorkflowDraft((draft) => ({
      ...draft,
      stages: draft.stages.map((stage, idx) => {
        if (idx !== stageIndex) return stage;
        const routes = stage.classify_routes.map((route, rIdx) =>
          rIdx === routeIndex ? { ...route, ...patch } : route,
        );
        return { ...stage, classify_routes: routes };
      }),
    }));
  };

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <BrandMark />
          <div>
            <div className="brand-title">Agent & Workflow Studio</div>
            <div className="brand-sub">Design agents, gates, handoffs, and agent chains</div>
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn-secondary" onClick={() => setAppPage("dashboard")}>
          Back to IDE
        </button>
      </header>

      <div style={{ display: "flex", gap: 0, flex: 1, minHeight: 0 }}>
        <aside
          style={{
            width: 240,
            borderRight: "1px solid var(--bd)",
            background: "var(--bg1)",
            padding: 12,
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <button
            type="button"
            className={`list-btn ${tab === "agents" ? "active" : ""}`}
            onClick={() => setTab("agents")}
          >
            Agent Studio
          </button>
          <button
            type="button"
            className={`list-btn ${tab === "workflows" ? "active" : ""}`}
            onClick={() => setTab("workflows")}
          >
            Workflow Studio
          </button>
        </aside>

        {tab === "agents" ? (
          <>
            <aside
              style={{
                width: 260,
                borderRight: "1px solid var(--bd)",
                background: "var(--bg0)",
                padding: 12,
                overflow: "auto",
              }}
            >
              <div className="state-label" style={{ marginBottom: 8 }}>
                Custom agents
              </div>
              <button
                type="button"
                className="btn-primary"
                style={{ width: "100%", marginBottom: 10 }}
                onClick={startNewAgent}
              >
                + New agent
              </button>
              {customAgents.map((agent) => (
                <button
                  key={agent.slug}
                  type="button"
                  className={`list-btn ${selectedAgentSlug === agent.slug ? "active" : ""}`}
                  onClick={() => setSelectedAgentSlug(agent.slug)}
                  style={{ marginBottom: 6, textAlign: "left" }}
                >
                  <div style={{ fontWeight: 600 }}>{agent.name}</div>
                  <div style={{ fontSize: 10.5, color: "var(--txl)", fontFamily: "var(--mono)" }}>{agent.slug}</div>
                </button>
              ))}

              <div className="state-label" style={{ margin: "14px 0 8px" }}>
                Built-in agents
              </div>
              {builtinAgents.map((agent) => (
                <button
                  key={agent.slug}
                  type="button"
                  className={`list-btn ${selectedAgentSlug === agent.slug ? "active" : ""}`}
                  onClick={() => setSelectedAgentSlug(agent.slug)}
                  style={{ marginBottom: 6, textAlign: "left" }}
                >
                  <div style={{ fontWeight: 600 }}>{agent.name}</div>
                  <div style={{ fontSize: 10.5, color: "var(--txl)", fontFamily: "var(--mono)" }}>{agent.slug}</div>
                </button>
              ))}
            </aside>

            <main style={{ flex: 1, overflow: "auto", padding: 20, minWidth: 0 }}>
              <h2 style={{ margin: "0 0 6px", fontFamily: "var(--dp)" }}>
                {isAgentReadOnly ? "Built-in agent" : selectedAgentSlug ? "Edit agent" : "Create agent"}
              </h2>
              <p className="modal-hint" style={{ marginTop: 0, marginBottom: 16 }}>
                {isAgentReadOnly
                  ? "Read-only registry agent. Duplicate to customize MCP tools, gates, and handoffs."
                  : "Define role instructions, Loregarden MCP tool access, gate checks, and handoff rules."}
              </p>

              {isAgentReadOnly && selectedAgent && (
                <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                  <button type="button" className="btn-secondary" onClick={() => duplicateAgent(selectedAgent)}>
                    Duplicate to custom
                  </button>
                  {selectedAgent.role_file && (
                    <span style={{ fontSize: 11, color: "var(--txl)", alignSelf: "center", fontFamily: "var(--mono)" }}>
                      {selectedAgent.role_file}
                    </span>
                  )}
                </div>
              )}

              <div className="modal-body" style={{ padding: 0, maxWidth: 860 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div className="modal-field">
                    <div className="modal-field-label">Name</div>
                    <input
                      className="btn-secondary"
                      style={{ width: "100%", boxSizing: "border-box" }}
                      value={isAgentReadOnly ? selectedAgent?.name ?? "" : agentDraft.name}
                      readOnly={isAgentReadOnly}
                      onChange={(e) => setAgentDraft({ ...agentDraft, name: e.target.value })}
                    />
                  </div>
                  <div className="modal-field">
                    <div className="modal-field-label">Slug</div>
                    <input
                      className="btn-secondary"
                      style={{ width: "100%", boxSizing: "border-box" }}
                      value={isAgentReadOnly ? selectedAgent?.slug ?? "" : agentDraft.slug}
                      placeholder="auto from name"
                      readOnly={isAgentReadOnly}
                      onChange={(e) => setAgentDraft({ ...agentDraft, slug: e.target.value })}
                    />
                  </div>
                </div>

                <div className="modal-field">
                  <div className="modal-field-label">Description</div>
                  <input
                    className="btn-secondary"
                    style={{ width: "100%", boxSizing: "border-box" }}
                    value={isAgentReadOnly ? selectedAgent?.description ?? "" : agentDraft.description}
                    readOnly={isAgentReadOnly}
                    onChange={(e) => setAgentDraft({ ...agentDraft, description: e.target.value })}
                  />
                </div>

                <div className="modal-field">
                  <div className="modal-field-label">Role instructions</div>
                  <textarea
                    className="btn-secondary"
                    style={{ width: "100%", minHeight: 160, boxSizing: "border-box", fontSize: 12.5 }}
                    value={isAgentReadOnly ? selectedAgent?.role_body ?? "" : agentDraft.role_body}
                    readOnly={isAgentReadOnly}
                    onChange={(e) => setAgentDraft({ ...agentDraft, role_body: e.target.value })}
                    placeholder="What this agent does, constraints, and output expectations…"
                  />
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                  <div className="modal-field">
                    <div className="modal-field-label">Provider</div>
                    <select
                      className="btn-secondary filter-select"
                      style={{ width: "100%" }}
                      value={isAgentReadOnly ? selectedAgent?.adapter ?? "claude" : agentDraft.adapter}
                      disabled={isAgentReadOnly}
                      onChange={(e) => setAgentDraft({ ...agentDraft, adapter: e.target.value })}
                    >
                      {ADAPTERS.map((opt) => (
                        <option key={opt.id} value={opt.id}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="modal-field">
                    <div className="modal-field-label">Default skill</div>
                    <select
                      className="btn-secondary filter-select"
                      style={{ width: "100%" }}
                      value={isAgentReadOnly ? selectedAgent?.default_skill ?? "" : agentDraft.default_skill}
                      disabled={isAgentReadOnly}
                      onChange={(e) => setAgentDraft({ ...agentDraft, default_skill: e.target.value })}
                    >
                      <option value="">—</option>
                      {(skills.data ?? []).map((skill) => (
                        <option key={skill} value={skill}>
                          {skill}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="modal-field">
                    <div className="modal-field-label">Timeout (sec)</div>
                    <input
                      type="number"
                      className="btn-secondary"
                      style={{ width: "100%", boxSizing: "border-box" }}
                      value={isAgentReadOnly ? selectedAgent?.timeout ?? 600 : agentDraft.timeout}
                      readOnly={isAgentReadOnly}
                      onChange={(e) => setAgentDraft({ ...agentDraft, timeout: Number(e.target.value) || 600 })}
                    />
                  </div>
                </div>

                {!isAgentReadOnly && (
                  <McpToolGuideSection
                    guides={mcpGuides.data ?? []}
                    enabled={agentDraft.mcp_enabled}
                    selected={agentDraft.mcp_tools}
                    onToggleEnabled={(enabled) =>
                      setAgentDraft({
                        ...agentDraft,
                        mcp_enabled: enabled,
                        mcp_tools: enabled ? studioDefaults.data?.mcp_tools ?? agentDraft.mcp_tools : [],
                      })
                    }
                    onToggleTool={toggleMcpTool}
                  />
                )}

                {isAgentReadOnly && selectedAgent?.mcp_enabled && (
                  <section style={{ marginTop: 16 }}>
                    <div className="modal-section-title">Enabled MCP tools</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                      {selectedAgent.mcp_tools.map((tool) => (
                        <span key={tool} className="state-card" style={{ padding: "4px 8px", fontFamily: "var(--mono)", fontSize: 10.5 }}>
                          {tool}
                        </span>
                      ))}
                    </div>
                  </section>
                )}

                {!isAgentReadOnly && (
                  <GateHandoffEditor
                    gateChecks={agentDraft.gate_checks}
                    handoffChecks={agentDraft.handoff_checks}
                    onChange={(gate_checks, handoff_checks) => setAgentDraft({ ...agentDraft, gate_checks, handoff_checks })}
                  />
                )}

                {!isAgentReadOnly && (
                  <div style={{ display: "flex", gap: 8, marginTop: 20 }}>
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={!agentDraft.name.trim() || saveAgent.isPending}
                      onClick={() => saveAgent.mutate()}
                    >
                      {saveAgent.isPending ? "Saving…" : isEditingCustomAgent ? "Save agent" : "Create agent"}
                    </button>
                    {isEditingCustomAgent && (
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={deleteAgent.isPending}
                        onClick={() => deleteAgent.mutate(selectedAgentSlug!)}
                      >
                        Delete
                      </button>
                    )}
                  </div>
                )}
              </div>
            </main>

            <AgentPreviewPanel preview={agentPreview.data} loading={agentPreview.isFetching} />
          </>
        ) : (
          <>
            <aside
              style={{
                width: 260,
                borderRight: "1px solid var(--bd)",
                background: "var(--bg0)",
                padding: 12,
                overflow: "auto",
              }}
            >
              <div className="state-label" style={{ marginBottom: 8 }}>
                Custom workflows
              </div>
              <button
                type="button"
                className="btn-primary"
                style={{ width: "100%", marginBottom: 10 }}
                onClick={() => {
                  setSelectedWorkflowSlug(null);
                  setWorkflowDraft({ slug: "", name: "", description: "", stages: [emptyStage(1)] });
                }}
              >
                + New workflow
              </button>
              {customWorkflows.map((workflow) => (
                <button
                  key={workflow.slug}
                  type="button"
                  className={`list-btn ${selectedWorkflowSlug === workflow.slug ? "active" : ""}`}
                  onClick={() => setSelectedWorkflowSlug(workflow.slug)}
                  style={{ marginBottom: 6, textAlign: "left" }}
                >
                  <div style={{ fontWeight: 600 }}>{workflow.name}</div>
                  <div style={{ fontSize: 10.5, color: "var(--txl)", fontFamily: "var(--mono)" }}>
                    {workflow.published_template_slug || workflow.slug}
                  </div>
                </button>
              ))}

              <div className="state-label" style={{ margin: "14px 0 8px" }}>
                Built-in workflows
              </div>
              {builtinWorkflows.map((workflow) => (
                <button
                  key={workflow.slug}
                  type="button"
                  className={`list-btn ${selectedWorkflowSlug === workflow.slug ? "active" : ""}`}
                  onClick={() => setSelectedWorkflowSlug(workflow.slug)}
                  style={{ marginBottom: 6, textAlign: "left" }}
                >
                  <div style={{ fontWeight: 600 }}>{workflow.name}</div>
                  <div style={{ fontSize: 10.5, color: "var(--txl)", fontFamily: "var(--mono)" }}>{workflow.slug}</div>
                </button>
              ))}
            </aside>

            <main style={{ flex: 1, overflow: "auto", padding: 20 }}>
              <h2 style={{ margin: "0 0 6px", fontFamily: "var(--dp)" }}>
                {isWorkflowReadOnly ? "Built-in workflow" : selectedWorkflowSlug ? "Edit workflow" : "Create workflow"}
              </h2>
              <p className="modal-hint" style={{ marginTop: 0, marginBottom: 16 }}>
                {isWorkflowReadOnly
                  ? "Read-only template. Duplicate to customize stages, classify routes, and gates."
                  : "Chain agents together. Add classify steps to route by language and specialty."}
              </p>

              {isWorkflowReadOnly && selectedWorkflow && (
                <div style={{ display: "flex", gap: 8, marginBottom: 14, alignItems: "center" }}>
                  <button type="button" className="btn-secondary" onClick={() => duplicateWorkflow(selectedWorkflow)}>
                    Duplicate to custom
                  </button>
                  {selectedWorkflow.source_path && (
                    <span style={{ fontSize: 11, color: "var(--txl)", fontFamily: "var(--mono)" }}>
                      {selectedWorkflow.source_path}
                    </span>
                  )}
                </div>
              )}

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, maxWidth: 860 }}>
                <div className="modal-field">
                  <div className="modal-field-label">Name</div>
                  <input
                    className="btn-secondary"
                    style={{ width: "100%", boxSizing: "border-box" }}
                    value={workflowDraft.name}
                    readOnly={isWorkflowReadOnly}
                    onChange={(e) => setWorkflowDraft({ ...workflowDraft, name: e.target.value })}
                  />
                </div>
                <div className="modal-field">
                  <div className="modal-field-label">Slug</div>
                  <input
                    className="btn-secondary"
                    style={{ width: "100%", boxSizing: "border-box" }}
                    value={workflowDraft.slug}
                    placeholder="auto from name"
                    readOnly={isWorkflowReadOnly}
                    onChange={(e) => setWorkflowDraft({ ...workflowDraft, slug: e.target.value })}
                  />
                </div>
              </div>

              <div className="modal-field" style={{ maxWidth: 860 }}>
                <div className="modal-field-label">Description</div>
                <input
                  className="btn-secondary"
                  style={{ width: "100%", boxSizing: "border-box" }}
                  value={workflowDraft.description}
                  readOnly={isWorkflowReadOnly}
                  onChange={(e) => setWorkflowDraft({ ...workflowDraft, description: e.target.value })}
                />
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "16px 0 10px" }}>
                <div className="modal-section-title" style={{ margin: 0 }}>
                  Stages
                </div>
                {!isWorkflowReadOnly && (
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    onClick={() =>
                      setWorkflowDraft((draft) => ({
                        ...draft,
                        stages: [...draft.stages, emptyStage(draft.stages.length + 1)],
                      }))
                    }
                  >
                    + Add stage
                  </button>
                )}
              </div>

              {workflowDraft.stages.map((stage, index) => (
                <div key={`${stage.key}-${index}`} className="state-card" style={{ marginBottom: 12, maxWidth: 920 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
                    <div className="modal-field">
                      <div className="modal-field-label">Stage key</div>
                      <input
                        className="btn-secondary"
                        style={{ width: "100%", boxSizing: "border-box" }}
                        value={stage.key}
                        readOnly={isWorkflowReadOnly}
                        onChange={(e) => updateStage(index, { key: e.target.value })}
                      />
                    </div>
                    <div className="modal-field">
                      <div className="modal-field-label">Label</div>
                      <input
                        className="btn-secondary"
                        style={{ width: "100%", boxSizing: "border-box" }}
                        value={stage.name}
                        readOnly={isWorkflowReadOnly}
                        onChange={(e) => updateStage(index, { name: e.target.value })}
                      />
                    </div>
                    <div className="modal-field">
                      <div className="modal-field-label">Step type</div>
                      <select
                        className="btn-secondary filter-select"
                        style={{ width: "100%" }}
                        value={stage.stage_type}
                        disabled={isWorkflowReadOnly}
                        onChange={(e) =>
                          updateStage(index, {
                            stage_type: e.target.value as StudioWorkflowStage["stage_type"],
                            classify_routes:
                              e.target.value === "classify" && stage.classify_routes.length === 0
                                ? [
                                    {
                                      languages: ["python"],
                                      specialties: ["backend"],
                                      agent_id: "backend_implementer",
                                      skill_name: "apply_patch",
                                      default: true,
                                    },
                                  ]
                                : stage.classify_routes,
                          })
                        }
                      >
                        <option value="agent">Agent</option>
                        <option value="classify">Classify & route</option>
                        <option value="gate">Gate / review</option>
                      </select>
                    </div>
                  </div>

                  {stage.stage_type === "agent" || stage.stage_type === "gate" ? (
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 8 }}>
                      <div className="modal-field">
                        <div className="modal-field-label">Agent</div>
                        <select
                          className="btn-secondary filter-select"
                          style={{ width: "100%" }}
                          value={stage.agent_id}
                          disabled={isWorkflowReadOnly}
                          onChange={(e) => updateStage(index, { agent_id: e.target.value })}
                        >
                          {agentOptions.map((opt) => (
                            <option key={opt.id} value={opt.id}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="modal-field">
                        <div className="modal-field-label">Skill</div>
                        <select
                          className="btn-secondary filter-select"
                          style={{ width: "100%" }}
                          value={stage.skill_name}
                          disabled={isWorkflowReadOnly}
                          onChange={(e) => updateStage(index, { skill_name: e.target.value })}
                        >
                          {(skills.data ?? []).map((skill) => (
                            <option key={skill} value={skill}>
                              {skill}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  ) : (
                    <div style={{ marginTop: 10 }}>
                      <div className="state-label" style={{ marginBottom: 8 }}>
                        Classification routes
                      </div>
                      {stage.classify_routes.map((route, routeIndex) => (
                        <div key={routeIndex} style={{ borderTop: "1px solid var(--bd)", paddingTop: 10, marginTop: 10 }}>
                          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                            <div className="modal-field">
                              <div className="modal-field-label">Languages</div>
                              <select
                                multiple
                                className="btn-secondary filter-select"
                                style={{ width: "100%", minHeight: 72 }}
                                value={route.languages}
                                disabled={isWorkflowReadOnly}
                                onChange={(e) =>
                                  updateRoute(
                                    index,
                                    routeIndex,
                                    { languages: Array.from(e.target.selectedOptions, (opt) => opt.value) },
                                  )
                                }
                              >
                                {LANGUAGE_OPTIONS.map((lang) => (
                                  <option key={lang} value={lang}>
                                    {lang}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="modal-field">
                              <div className="modal-field-label">Specialties</div>
                              <select
                                multiple
                                className="btn-secondary filter-select"
                                style={{ width: "100%", minHeight: 72 }}
                                value={route.specialties}
                                disabled={isWorkflowReadOnly}
                                onChange={(e) =>
                                  updateRoute(
                                    index,
                                    routeIndex,
                                    { specialties: Array.from(e.target.selectedOptions, (opt) => opt.value) },
                                  )
                                }
                              >
                                {SPECIALTY_OPTIONS.map((spec) => (
                                  <option key={spec} value={spec}>
                                    {spec}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </div>
                          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 10, marginTop: 8 }}>
                            <select
                              className="btn-secondary filter-select"
                              value={route.agent_id}
                              disabled={isWorkflowReadOnly}
                              onChange={(e) => updateRoute(index, routeIndex, { agent_id: e.target.value })}
                            >
                              {agentOptions.map((opt) => (
                                <option key={opt.id} value={opt.id}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                            <select
                              className="btn-secondary filter-select"
                              value={route.skill_name}
                              disabled={isWorkflowReadOnly}
                              onChange={(e) => updateRoute(index, routeIndex, { skill_name: e.target.value })}
                            >
                              {(skills.data ?? []).map((skill) => (
                                <option key={skill} value={skill}>
                                  {skill}
                                </option>
                              ))}
                            </select>
                            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5 }}>
                              <input
                                type="checkbox"
                                checked={route.default}
                                disabled={isWorkflowReadOnly}
                                onChange={(e) => updateRoute(index, routeIndex, { default: e.target.checked })}
                              />
                              Default
                            </label>
                          </div>
                        </div>
                      ))}
                      {!isWorkflowReadOnly && (
                        <button
                          type="button"
                          className="btn-secondary btn-compact"
                          style={{ marginTop: 8 }}
                          onClick={() =>
                            updateStage(index, {
                              classify_routes: [
                                ...stage.classify_routes,
                                {
                                  languages: [],
                                  specialties: [],
                                  agent_id: "backend_implementer",
                                  skill_name: "apply_patch",
                                  default: false,
                                },
                              ],
                            })
                          }
                        >
                          + Add route
                        </button>
                      )}
                    </div>
                  )}

                  <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, fontSize: 11.5 }}>
                    <input
                      type="checkbox"
                      checked={stage.gate_required}
                      disabled={isWorkflowReadOnly}
                      onChange={(e) => updateStage(index, { gate_required: e.target.checked })}
                    />
                    Require gate approval before leaving this stage
                  </label>
                </div>
              ))}

              {!isWorkflowReadOnly && (
                <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={!workflowDraft.name.trim() || saveWorkflow.isPending}
                    onClick={() => saveWorkflow.mutate()}
                  >
                    {saveWorkflow.isPending ? "Saving…" : selectedWorkflowSlug ? "Save workflow" : "Create workflow"}
                  </button>
                  {selectedWorkflowSlug && !isWorkflowReadOnly && (
                    <>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={publishWorkflow.isPending}
                        onClick={() => publishWorkflow.mutate(selectedWorkflowSlug)}
                      >
                        {publishWorkflow.isPending ? "Publishing…" : "Publish to templates"}
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={deleteWorkflow.isPending}
                        onClick={() => deleteWorkflow.mutate(selectedWorkflowSlug)}
                      >
                        Delete
                      </button>
                    </>
                  )}
                </div>
              )}
            </main>
          </>
        )}
      </div>
    </div>
  );
}
