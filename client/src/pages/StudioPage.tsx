import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  type ClassifyRoute,
  type StudioAgent,
  type StudioGateCheck,
  type StudioHandoffCheck,
  type StudioWorkflow,
  type StudioWorkflowStage,
} from "../api/client";
import { AppTopbarActions } from "../components/AppTopbarActions";
import { AgentPreviewPanel } from "../components/studio/AgentPreviewPanel";
import { StageRouteHints } from "../components/StageRouteHints";
import { McpToolGuideSection } from "../components/studio/McpToolGuideSection";
import { GateHandoffEditor } from "../components/studio/GateHandoffEditor";
import { StudioDescribeBar } from "../components/studio/StudioDescribeBar";
import { TicketStudioPanel } from "../components/studio/TicketStudioPanel";
import { WorkflowPreviewPanel } from "../components/studio/WorkflowPreviewPanel";
import { navigateToStudio, navigateToStudioAgent, navigateToStudioAgentNew, navigateToStudioWorkflow, navigateToStudioWorkflowNew, useStudioResourceFromRoute, useStudioSectionFromRoute } from "../lib/useAppNavigation";
import { isStudioNewResource, studioPath } from "../lib/appNavigation";

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
  default_model: "",
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
    model: "",
  };
}

function agentCategory(agent: StudioAgent): { label: string; className: string } {
  if (!agent.built_in) return { label: "Custom", className: "custom" };
  const slug = agent.slug.toLowerCase();
  if (slug.includes("plan")) return { label: "Planning", className: "planning" };
  if (slug.includes("review")) return { label: "Review", className: "review" };
  if (slug.includes("implement") || slug.includes("coder") || slug.includes("backend") || slug.includes("frontend")) {
    return { label: "Implementation", className: "implementation" };
  }
  if (slug.includes("test") || slug.includes("qa")) return { label: "Testing", className: "testing" };
  return { label: "Agent", className: "default" };
}

function stageTypeClass(type: StudioWorkflowStage["stage_type"]): string {
  if (type === "classify") return "classify";
  if (type === "gate") return "gate";
  return "agent";
}

export function StudioPage() {
  const qc = useQueryClient();
  const tab = useStudioSectionFromRoute();
  const studioResourceId = useStudioResourceFromRoute();
  const isNewAgent = tab === "agents" && isStudioNewResource(studioResourceId);
  const isNewWorkflow = tab === "workflows" && isStudioNewResource(studioResourceId);
  const selectedAgentSlug =
    tab === "agents" && studioResourceId && !isStudioNewResource(studioResourceId)
      ? studioResourceId
      : null;
  const selectedWorkflowSlug =
    tab === "workflows" && studioResourceId && !isStudioNewResource(studioResourceId)
      ? studioResourceId
      : null;
  const isCreatingAgent = tab === "agents" && !selectedAgentSlug;
  const isCreatingWorkflow = tab === "workflows" && !selectedWorkflowSlug;
  const [layoutMode, setLayoutMode] = useState<"workbench" | "focus">("workbench");
  const [agentDraft, setAgentDraft] = useState({ ...EMPTY_AGENT });
  const [agentDescribePrompt, setAgentDescribePrompt] = useState("");
  const [workflowDescribePrompt, setWorkflowDescribePrompt] = useState("");
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
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const runtimeOptions = useQuery({ queryKey: ["runtime-options"], queryFn: api.runtimeOptions });

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
  const prevIsNewAgentRef = useRef(false);
  const prevIsNewWorkflowRef = useRef(false);
  const skipAgentDraftResetRef = useRef(false);
  const skipWorkflowDraftResetRef = useRef(false);

  useEffect(() => {
    const source = isAgentReadOnly && selectedAgent
      ? {
          name: selectedAgent.name,
          description: selectedAgent.description,
          role_body: selectedAgent.role_body,
          adapter: selectedAgent.adapter,
          default_model: selectedAgent.default_model,
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
          default_model: agentDraft.default_model,
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
      navigateToStudioAgent(saved.slug, true);
    },
  });

  const deleteAgent = useMutation({
    mutationFn: (slug: string) => api.deleteStudioAgent(slug),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["studio-agents"] });
      navigateToStudio("agents", true);
      setAgentDraft({ ...EMPTY_AGENT });
    },
  });

  const generateAgentDraft = useMutation({
    mutationFn: () => api.generateStudioAgent(agentDescribePrompt),
    onSuccess: (generated) => {
      setAgentDraft((draft) => ({
        ...draft,
        slug: generated.slug || draft.slug,
        name: generated.name,
        description: generated.description,
        role_body: generated.role_body,
        adapter: generated.adapter || draft.adapter,
        default_skill: generated.default_skill || draft.default_skill,
        mcp_tools: generated.mcp_tools.length ? generated.mcp_tools : draft.mcp_tools,
        mcp_enabled: generated.mcp_tools.length ? true : draft.mcp_enabled,
      }));
    },
  });

  const generateWorkflowDraft = useMutation({
    mutationFn: () => api.generateStudioWorkflow(workflowDescribePrompt),
    onSuccess: (generated) => {
      setWorkflowDraft({
        slug: generated.slug,
        name: generated.name,
        description: generated.description,
        stages: generated.stages.length ? generated.stages : [emptyStage(1)],
      });
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
      navigateToStudioWorkflow(saved.slug, true);
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
      navigateToStudio("workflows", true);
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
      default_model: agent.default_model,
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

  useEffect(() => {
    const enteredNew = isNewAgent && !prevIsNewAgentRef.current;
    prevIsNewAgentRef.current = isNewAgent;
    if (!enteredNew) return;
    if (skipAgentDraftResetRef.current) {
      skipAgentDraftResetRef.current = false;
      return;
    }
    const defaults = studioDefaults.data;
    setAgentDraft({
      ...EMPTY_AGENT,
      mcp_tools: defaults?.mcp_tools ?? [],
      handoff_checks: defaults?.handoff_checks ?? [],
      gate_checks: defaults?.gate_checks ?? [],
    });
    setAgentDescribePrompt("");
  }, [isNewAgent, studioDefaults.data]);

  useEffect(() => {
    const enteredNew = isNewWorkflow && !prevIsNewWorkflowRef.current;
    prevIsNewWorkflowRef.current = isNewWorkflow;
    if (!enteredNew) return;
    if (skipWorkflowDraftResetRef.current) {
      skipWorkflowDraftResetRef.current = false;
      return;
    }
    setWorkflowDraft({ slug: "", name: "", description: "", stages: [emptyStage(1)] });
    setWorkflowDescribePrompt("");
  }, [isNewWorkflow]);

  useEffect(() => {
    if (tab === "agents" && studioResourceId === null) {
      navigateToStudioAgentNew(true);
    }
  }, [tab, studioResourceId]);

  useEffect(() => {
    if (tab === "workflows" && studioResourceId === null) {
      navigateToStudioWorkflowNew(true);
    }
  }, [tab, studioResourceId]);

  useEffect(() => {
    if (tab !== "agents" || !selectedAgentSlug || !agents.data) return;
    if (!agents.data.some((agent) => agent.slug === selectedAgentSlug)) {
      navigateToStudio("agents", true);
    }
  }, [tab, selectedAgentSlug, agents.data]);

  useEffect(() => {
    if (tab !== "workflows" || !selectedWorkflowSlug || !workflows.data) return;
    if (!workflows.data.some((workflow) => workflow.slug === selectedWorkflowSlug)) {
      navigateToStudio("workflows", true);
    }
  }, [tab, selectedWorkflowSlug, workflows.data]);

  const startNewAgent = () => {
    navigateToStudioAgentNew();
  };

  const duplicateAgent = (agent: StudioAgent) => {
    skipAgentDraftResetRef.current = true;
    navigateToStudioAgentNew();
    setAgentDraft({
      slug: `${agent.slug}-copy`,
      name: `${agent.name} (copy)`,
      description: agent.description,
      role_body: agent.role_body,
      adapter: agent.adapter,
      default_model: agent.default_model,
      timeout: agent.timeout,
      default_skill: agent.default_skill,
      mcp_enabled: agent.mcp_enabled,
      mcp_tools: agent.mcp_tools,
      gate_checks: agent.gate_checks,
      handoff_checks: agent.handoff_checks,
    });
  };

  const duplicateWorkflow = (workflow: StudioWorkflow) => {
    skipWorkflowDraftResetRef.current = true;
    navigateToStudioWorkflowNew();
    setWorkflowDraft({
      slug: `${workflow.slug}-copy`,
      name: `${workflow.name} (copy)`,
      description: workflow.description,
      stages: workflow.stages.map((stage, idx) => ({ ...stage, order: idx + 1 })),
    });
  };

  const startNewWorkflow = () => {
    navigateToStudioWorkflowNew();
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
    <div className="screen-view screen-view--studio">
      <header className="page-hero-header">
        <div className="page-hero-copy">
          <div className="page-hero-eyebrow">
            <span>Studios</span>
            <span className="page-hero-eyebrow-dot" aria-hidden />
            <span className="page-hero-eyebrow-muted">Agents · Workflows · Scoping</span>
          </div>
          <h1 className="page-hero-title">
            {tab === "agents" ? "Agent Studio" : tab === "workflows" ? "Workflow Studio" : "Ticket Studio"}
          </h1>
          <p className="page-hero-sub">
            {tab === "agents"
              ? "Define role instructions, MCP tools, gates, and handoff rules."
              : tab === "workflows"
                ? "Chain agents together with classify steps, gates, and human approvals."
                : "Chat with the scoper to refine scope and generate draft tickets."}
          </p>
        </div>
        <div className="page-hero-actions">
          {(tab === "agents" || tab === "workflows") && (
            <div className="studio-layout-toggle" role="group" aria-label="Layout mode">
              <button
                type="button"
                className={layoutMode === "workbench" ? "active" : ""}
                onClick={() => setLayoutMode("workbench")}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
                  <rect x="3" y="3" width="6" height="18" rx="1.5" />
                  <rect x="11" y="3" width="10" height="10" rx="1.5" />
                  <rect x="11" y="15" width="10" height="6" rx="1.5" />
                </svg>
                Workbench
              </button>
              <button
                type="button"
                className={layoutMode === "focus" ? "active" : ""}
                onClick={() => setLayoutMode("focus")}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
                  <rect x="3" y="3" width="12" height="18" rx="1.5" />
                  <rect x="17" y="3" width="4" height="18" rx="1.5" />
                </svg>
                Focus
              </button>
            </div>
          )}
          <AppTopbarActions />
        </div>
      </header>

      <div className="studio-subtabs" role="tablist" aria-label="Studio sections">
        {(
          [
            ["agents", "Agent Studio"],
            ["workflows", "Workflow Studio"],
            ["tickets", "Ticket Studio"],
          ] as const
        ).map(([section, label]) => (
          <NavLink
            key={section}
            to={studioPath(section)}
            role="tab"
            aria-selected={tab === section}
            className={`studio-subtab${tab === section ? " active" : ""}`}
          >
            {label}
          </NavLink>
        ))}
      </div>

      <div className="studio-body">
        {tab === "tickets" ? (
          <div className="studio-shell">
            <TicketStudioPanel
              workspaces={workspaces.data ?? []}
              runtimeOptions={runtimeOptions.data}
            />
          </div>
        ) : tab === "agents" ? (
          <div className="studio-shell">
            {layoutMode === "workbench" && (
              <aside className="studio-library-rail">
                <button type="button" className="studio-library-cta" onClick={startNewAgent}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                  New agent
                </button>
                {customAgents.length > 0 && (
                  <>
                    <div className="studio-library-section-label">Custom agents</div>
                    <div className="studio-library-list" style={{ marginBottom: 14 }}>
                      {customAgents.map((agent) => {
                        const cat = agentCategory(agent);
                        return (
                          <button
                            key={agent.slug}
                            type="button"
                            className={`studio-library-item${selectedAgentSlug === agent.slug ? " active" : ""}`}
                            onClick={() => navigateToStudioAgent(agent.slug)}
                          >
                            <span className="studio-library-item-name">{agent.name}</span>
                            <div className="studio-library-item-meta">
                              <span className={`studio-library-item-cat ${cat.className}`}>{cat.label}</span>
                              <span className="studio-library-item-slug">{agent.slug}</span>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}
                <div className="studio-library-section-label">Built-in agents</div>
                <div className="studio-library-list">
                  {builtinAgents.map((agent) => {
                    const cat = agentCategory(agent);
                    return (
                      <button
                        key={agent.slug}
                        type="button"
                        className={`studio-library-item${selectedAgentSlug === agent.slug ? " active" : ""}`}
                        onClick={() => navigateToStudioAgent(agent.slug)}
                      >
                        <span className="studio-library-item-name">{agent.name}</span>
                        <div className="studio-library-item-meta">
                          <span className={`studio-library-item-cat ${cat.className}`}>{cat.label}</span>
                          <span className="studio-library-item-slug">{agent.slug}</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </aside>
            )}

            <div className="studio-editor">
              <div className="studio-editor-inner studio-editor-inner--agent">
              {layoutMode === "focus" && (
                <div className="studio-focus-chips">
                  <button type="button" className="studio-focus-chip-new" onClick={startNewAgent}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                      <path d="M12 5v14M5 12h14" />
                    </svg>
                    New
                  </button>
                  {[...customAgents, ...builtinAgents].map((agent) => (
                    <button
                      key={agent.slug}
                      type="button"
                      className={`studio-focus-chip${selectedAgentSlug === agent.slug ? " active" : ""}`}
                      onClick={() => navigateToStudioAgent(agent.slug)}
                    >
                      {agent.name}
                    </button>
                  ))}
                </div>
              )}

              {isAgentReadOnly && selectedAgent && (
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <button type="button" className="btn-secondary" onClick={() => duplicateAgent(selectedAgent)}>
                    Duplicate to custom
                  </button>
                  {selectedAgent.role_file && (
                    <span style={{ fontSize: 11, color: "var(--txl)", fontFamily: "var(--mono)" }}>
                      {selectedAgent.role_file}
                    </span>
                  )}
                </div>
              )}

              {isCreatingAgent && (
                <StudioDescribeBar
                  value={agentDescribePrompt}
                  onChange={setAgentDescribePrompt}
                  onGenerate={() => generateAgentDraft.mutate()}
                  placeholder="Describe the agent you want — role, constraints, when to use it…"
                  generateLabel="Generate agent"
                  pending={generateAgentDraft.isPending}
                  error={generateAgentDraft.isError ? (generateAgentDraft.error as Error).message : null}
                />
              )}

              <div className="studio-card">
                <div className="studio-card-header">
                  <span className="studio-card-icon teal" aria-hidden>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="8" r="4" />
                      <path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1" />
                    </svg>
                  </span>
                  <span className="studio-card-title">Identity</span>
                </div>
                <div className="studio-field-row">
                  <div className="studio-field">
                    <div className="studio-field-label">Name</div>
                    <input
                      className="studio-input"
                      value={isAgentReadOnly ? selectedAgent?.name ?? "" : agentDraft.name}
                      readOnly={isAgentReadOnly}
                      placeholder="e.g. Localization Reviewer"
                      onChange={(e) => setAgentDraft({ ...agentDraft, name: e.target.value })}
                    />
                  </div>
                  <div className="studio-field">
                    <div className="studio-field-label">Slug</div>
                    <input
                      className="studio-input mono"
                      value={isAgentReadOnly ? selectedAgent?.slug ?? "" : agentDraft.slug}
                      placeholder="auto from name"
                      readOnly={isAgentReadOnly}
                      onChange={(e) => setAgentDraft({ ...agentDraft, slug: e.target.value })}
                    />
                  </div>
                </div>
                <div className="studio-field">
                  <div className="studio-field-label">Description</div>
                  <input
                    className="studio-input"
                    value={isAgentReadOnly ? selectedAgent?.description ?? "" : agentDraft.description}
                    readOnly={isAgentReadOnly}
                    placeholder="One line — when should the orchestrator reach for this agent?"
                    onChange={(e) => setAgentDraft({ ...agentDraft, description: e.target.value })}
                  />
                </div>
              </div>

              <div className="studio-card">
                <div className="studio-card-header tight">
                  <span className="studio-card-icon violet" aria-hidden>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M12 3a3 3 0 0 0-3 3v1a3 3 0 0 0-3 3 3 3 0 0 0 1 5.8V18a3 3 0 0 0 5 2 3 3 0 0 0 5-2v-2.2A3 3 0 0 0 18 10a3 3 0 0 0-3-3V6a3 3 0 0 0-3-3z" />
                    </svg>
                  </span>
                  <span className="studio-card-title">Role instructions</span>
                </div>
                <p className="studio-card-hint">
                  What this agent does, its constraints, and its output expectations. This becomes the system prompt.
                </p>
                <textarea
                  className="studio-textarea"
                  value={isAgentReadOnly ? selectedAgent?.role_body ?? "" : agentDraft.role_body}
                  readOnly={isAgentReadOnly}
                  onChange={(e) => setAgentDraft({ ...agentDraft, role_body: e.target.value })}
                  placeholder="Review the staged diff against the ticket's acceptance criteria…"
                />
              </div>

              <div className="studio-card">
                <div className="studio-card-header">
                  <span className="studio-card-icon blue" aria-hidden>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M13 2 3 14h9l-1 8 10-12h-9z" />
                    </svg>
                  </span>
                  <span className="studio-card-title">Runtime</span>
                </div>
                <div className="studio-field-row" style={{ marginBottom: 0 }}>
                  <div className="studio-field" style={{ flex: 1.3 }}>
                    <div className="studio-field-label">Provider</div>
                    <select
                      className="studio-select"
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
                  <div className="studio-field">
                    <div className="studio-field-label">Default skill</div>
                    <select
                      className="studio-select mono"
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
                  <div className="studio-field">
                    <div className="studio-field-label">Default model</div>
                    {(() => {
                      const adapter = isAgentReadOnly ? selectedAgent?.adapter ?? "claude" : agentDraft.adapter;
                      const value = isAgentReadOnly
                        ? selectedAgent?.default_model ?? ""
                        : agentDraft.default_model;
                      const modelOptions =
                        adapter === "cursor"
                          ? runtimeOptions.data?.cursor_models
                          : adapter === "claude"
                            ? runtimeOptions.data?.claude_models
                            : undefined;
                      if (modelOptions) {
                        return (
                          <select
                            className="studio-select mono"
                            value={value}
                            disabled={isAgentReadOnly}
                            onChange={(e) => setAgentDraft({ ...agentDraft, default_model: e.target.value })}
                          >
                            {modelOptions.map((opt) => (
                              <option key={opt.id || "default"} value={opt.id}>
                                {opt.label}
                              </option>
                            ))}
                          </select>
                        );
                      }
                      return (
                        <input
                          className="studio-input mono"
                          placeholder="Model id"
                          value={value}
                          readOnly={isAgentReadOnly}
                          onChange={(e) => setAgentDraft({ ...agentDraft, default_model: e.target.value })}
                        />
                      );
                    })()}
                  </div>
                  <div className="studio-field" style={{ flex: 0.8 }}>
                    <div className="studio-field-label">Timeout (s)</div>
                    <input
                      type="number"
                      className="studio-input mono"
                      value={isAgentReadOnly ? selectedAgent?.timeout ?? 600 : agentDraft.timeout}
                      readOnly={isAgentReadOnly}
                      onChange={(e) => setAgentDraft({ ...agentDraft, timeout: Number(e.target.value) || 600 })}
                    />
                  </div>
                </div>
              </div>

              {!isAgentReadOnly ? (
                <McpToolGuideSection
                  variant="studio"
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
              ) : selectedAgent?.mcp_enabled ? (
                <div className="studio-card">
                  <div className="studio-card-header">
                    <span className="studio-card-title">Enabled MCP tools</span>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {selectedAgent.mcp_tools.map((tool) => (
                      <span key={tool} className="studio-preview-chip">
                        {tool}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}

              {!isAgentReadOnly && (
                <div className="studio-card">
                  <GateHandoffEditor
                    gateChecks={agentDraft.gate_checks}
                    handoffChecks={agentDraft.handoff_checks}
                    onChange={(gate_checks, handoff_checks) => setAgentDraft({ ...agentDraft, gate_checks, handoff_checks })}
                  />
                </div>
              )}

              {!isAgentReadOnly && (
                <div className="studio-card-actions">
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
                  <button
                    type="button"
                    className="btn-primary btn-cta"
                    disabled={!agentDraft.name.trim() || saveAgent.isPending}
                    onClick={() => saveAgent.mutate()}
                  >
                    {saveAgent.isPending ? "Saving…" : isEditingCustomAgent ? "Save agent" : "Create agent"}
                  </button>
                </div>
              )}
              </div>
            </div>

            <AgentPreviewPanel
              preview={agentPreview.data}
              loading={agentPreview.isFetching}
              slug={isAgentReadOnly ? selectedAgent?.slug : agentDraft.slug || selectedAgentSlug || undefined}
            />
          </div>
        ) : (
          <div className="studio-shell">
            {layoutMode === "workbench" && (
              <aside className="studio-library-rail">
                <button
                  type="button"
                  className="studio-library-cta"
                  onClick={startNewWorkflow}
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                  New workflow
                </button>
                {customWorkflows.length > 0 && (
                  <>
                    <div className="studio-library-section-label">Custom workflows</div>
                    <div className="studio-library-list" style={{ marginBottom: 14 }}>
                      {customWorkflows.map((workflow) => (
                        <button
                          key={workflow.slug}
                          type="button"
                          className={`studio-library-item${selectedWorkflowSlug === workflow.slug ? " active" : ""}`}
                          onClick={() => navigateToStudioWorkflow(workflow.slug)}
                        >
                          <span className="studio-library-item-name">{workflow.name}</span>
                          <div className="studio-library-item-meta">
                            <span className="studio-library-item-slug">
                              {workflow.published_template_slug || workflow.slug}
                            </span>
                          </div>
                        </button>
                      ))}
                    </div>
                  </>
                )}
                <div className="studio-library-section-label">Built-in workflows</div>
                <div className="studio-library-list">
                  {builtinWorkflows.map((workflow) => (
                    <button
                      key={workflow.slug}
                      type="button"
                      className={`studio-library-item${selectedWorkflowSlug === workflow.slug ? " active" : ""}`}
                      onClick={() => navigateToStudioWorkflow(workflow.slug)}
                    >
                      <span className="studio-library-item-name">{workflow.name}</span>
                      <div className="studio-library-item-meta">
                        <span className="studio-library-item-slug">{workflow.slug}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </aside>
            )}

            <div className="studio-editor">
              <div className="studio-editor-inner studio-editor-inner--workflow">
              {layoutMode === "focus" && (
                <div className="studio-focus-chips">
                  <button
                    type="button"
                    className="studio-focus-chip-new"
                    onClick={startNewWorkflow}
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                      <path d="M12 5v14M5 12h14" />
                    </svg>
                    New
                  </button>
                  {[...customWorkflows, ...builtinWorkflows].map((workflow) => (
                    <button
                      key={workflow.slug}
                      type="button"
                      className={`studio-focus-chip${selectedWorkflowSlug === workflow.slug ? " active" : ""}`}
                      onClick={() => navigateToStudioWorkflow(workflow.slug)}
                    >
                      {workflow.name}
                    </button>
                  ))}
                </div>
              )}

              {isWorkflowReadOnly && selectedWorkflow && (
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
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

              {isCreatingWorkflow && (
                <StudioDescribeBar
                  value={workflowDescribePrompt}
                  onChange={setWorkflowDescribePrompt}
                  onGenerate={() => generateWorkflowDraft.mutate()}
                  placeholder="Describe the workflow you want — stages, agents, gates, routing…"
                  generateLabel="Generate workflow"
                  pending={generateWorkflowDraft.isPending}
                  error={generateWorkflowDraft.isError ? (generateWorkflowDraft.error as Error).message : null}
                />
              )}

              <div className="studio-card">
                <div className="studio-card-header">
                  <span className="studio-card-icon teal" aria-hidden>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="6" cy="6" r="2.5" />
                      <circle cx="6" cy="18" r="2.5" />
                      <path d="M6 8.5v7M18 6H9M18 6a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18 6v6a6 6 0 0 1-6 6" />
                    </svg>
                  </span>
                  <span className="studio-card-title">Workflow identity</span>
                </div>
                <div className="studio-field-row">
                  <div className="studio-field">
                    <div className="studio-field-label">Name</div>
                    <input
                      className="studio-input"
                      value={workflowDraft.name}
                      readOnly={isWorkflowReadOnly}
                      placeholder="e.g. Hotfix express"
                      onChange={(e) => setWorkflowDraft({ ...workflowDraft, name: e.target.value })}
                    />
                  </div>
                  <div className="studio-field">
                    <div className="studio-field-label">Slug</div>
                    <input
                      className="studio-input mono"
                      value={workflowDraft.slug}
                      placeholder="auto from name"
                      readOnly={isWorkflowReadOnly}
                      onChange={(e) => setWorkflowDraft({ ...workflowDraft, slug: e.target.value })}
                    />
                  </div>
                </div>
                <div className="studio-field">
                  <div className="studio-field-label">Description</div>
                  <input
                    className="studio-input"
                    value={workflowDraft.description}
                    readOnly={isWorkflowReadOnly}
                    placeholder="Chain agents together. Add classify steps to route by language and specialty."
                    onChange={(e) => setWorkflowDraft({ ...workflowDraft, description: e.target.value })}
                  />
                </div>
              </div>

              <div className="studio-card">
                <div className="studio-card-header">
                  <span className="studio-card-title">Stages</span>
                  <span className="studio-stage-count">{workflowDraft.stages.length}</span>
                  <div style={{ flex: 1 }} />
                  {!isWorkflowReadOnly && (
                    <button
                      type="button"
                      className="studio-add-stage-btn"
                      onClick={() =>
                        setWorkflowDraft((draft) => ({
                          ...draft,
                          stages: [...draft.stages, emptyStage(draft.stages.length + 1)],
                        }))
                      }
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                        <path d="M12 5v14M5 12h14" />
                      </svg>
                      Add stage
                    </button>
                  )}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {workflowDraft.stages.map((stage, index) => {
                    const typeClass = stageTypeClass(stage.stage_type);
                    const typeLabel =
                      stage.stage_type === "classify" ? "Classify" : stage.stage_type === "gate" ? "Gate" : "Agent";
                    return (
                      <div key={`${stage.key}-${index}`} className={`studio-stage-card ${typeClass}`}>
                        <div className="studio-stage-header">
                          <span className="studio-stage-num">{index + 1}</span>
                          <span style={{ fontFamily: "var(--dp)", fontSize: 13, fontWeight: 600, color: "var(--tx)" }}>
                            {stage.name || `Stage ${index + 1}`}
                          </span>
                          <span className={`studio-stage-type-badge ${typeClass}`}>{typeLabel}</span>
                          <div style={{ flex: 1 }} />
                          {!isWorkflowReadOnly && (
                            <>
                              <button
                                type="button"
                                className="studio-stage-remove"
                                aria-label={`Move stage ${index + 1} up`}
                                disabled={index === 0}
                                onClick={() =>
                                  setWorkflowDraft((draft) => {
                                    const stages = [...draft.stages];
                                    [stages[index - 1], stages[index]] = [stages[index], stages[index - 1]];
                                    return { ...draft, stages };
                                  })
                                }
                              >
                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                                  <path d="M12 19V5M5 12l7-7 7 7" />
                                </svg>
                              </button>
                              <button
                                type="button"
                                className="studio-stage-remove"
                                aria-label={`Move stage ${index + 1} down`}
                                disabled={index === workflowDraft.stages.length - 1}
                                onClick={() =>
                                  setWorkflowDraft((draft) => {
                                    const stages = [...draft.stages];
                                    [stages[index], stages[index + 1]] = [stages[index + 1], stages[index]];
                                    return { ...draft, stages };
                                  })
                                }
                              >
                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                                  <path d="M12 5v14M5 12l7 7 7-7" />
                                </svg>
                              </button>
                            </>
                          )}
                          {!isWorkflowReadOnly && workflowDraft.stages.length > 1 && (
                            <button
                              type="button"
                              className="studio-stage-remove"
                              aria-label={`Remove stage ${index + 1}`}
                              onClick={() =>
                                setWorkflowDraft((draft) => ({
                                  ...draft,
                                  stages: draft.stages.filter((_, idx) => idx !== index),
                                }))
                              }
                            >
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                              </svg>
                            </button>
                          )}
                        </div>
                        <div className="studio-stage-fields">
                          <div>
                            <div className="studio-stage-field-label">Stage key</div>
                            <input
                              className="studio-stage-input mono"
                              value={stage.key}
                              readOnly={isWorkflowReadOnly}
                              onChange={(e) => updateStage(index, { key: e.target.value })}
                            />
                          </div>
                          <div>
                            <div className="studio-stage-field-label">Label</div>
                            <input
                              className="studio-stage-input"
                              value={stage.name}
                              readOnly={isWorkflowReadOnly}
                              onChange={(e) => updateStage(index, { name: e.target.value })}
                            />
                          </div>
                          <div>
                            <div className="studio-stage-field-label">Step type</div>
                            <select
                              className="studio-stage-select"
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

                        {selectedWorkflow?.transitions?.length ? (
                          <StageRouteHints
                            stage={{
                              key: stage.key,
                              name: stage.name,
                              status: "pending",
                              order: stage.order,
                              agent_id: stage.agent_id,
                              skill_name: stage.skill_name,
                              optional: stage.optional,
                              note: "",
                              stage_type: stage.stage_type,
                              agents: [],
                              model: stage.model,
                            }}
                            transitions={selectedWorkflow.transitions}
                            stages={workflowDraft.stages.map((item, stageIndex) => ({
                              key: item.key,
                              name: item.name,
                              status: "pending" as const,
                              order: item.order || stageIndex + 1,
                              agent_id: item.agent_id,
                              skill_name: item.skill_name,
                              optional: item.optional,
                              note: "",
                              stage_type: item.stage_type,
                              agents: [],
                              model: item.model,
                            }))}
                          />
                        ) : null}

                        {stage.stage_type === "agent" || stage.stage_type === "gate" ? (
                          <div className="studio-stage-fields">
                            <div>
                              <div className="studio-stage-field-label">Agent</div>
                              <select
                                className="studio-stage-select"
                                value={stage.agent_id}
                                disabled={isWorkflowReadOnly}
                                onChange={(e) => updateStage(index, { agent_id: e.target.value })}
                              >
                                {stage.stage_type === "agent" && (
                                  <option value="">— None (human approval) —</option>
                                )}
                                {agentOptions.map((opt) => (
                                  <option key={opt.id} value={opt.id}>
                                    {opt.label}
                                  </option>
                                ))}
                              </select>
                              {stage.stage_type === "agent" && !stage.agent_id && (
                                <div style={{ marginTop: 4, fontSize: 11, color: "var(--txl)" }}>
                                  No agent runs — the ticket pauses here until a human approves in Triage/Inbox.
                                </div>
                              )}
                            </div>
                            <div>
                              <div className="studio-stage-field-label">Skill</div>
                              <select
                                className="studio-stage-select mono"
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
                            <div>
                              <div className="studio-stage-field-label">Model override</div>
                              {(() => {
                                const stageAgent = agents.data?.find((a) => a.slug === stage.agent_id);
                                const modelOptions =
                                  stageAgent?.adapter === "cursor"
                                    ? runtimeOptions.data?.cursor_models
                                    : stageAgent?.adapter === "claude" || !stageAgent
                                      ? runtimeOptions.data?.claude_models
                                      : undefined;
                                if (modelOptions) {
                                  return (
                                    <select
                                      className="studio-stage-select mono"
                                      value={stage.model}
                                      disabled={isWorkflowReadOnly}
                                      onChange={(e) => updateStage(index, { model: e.target.value })}
                                    >
                                      <option value="">— Agent default —</option>
                                      {modelOptions
                                        .filter((opt) => opt.id)
                                        .map((opt) => (
                                          <option key={opt.id} value={opt.id}>
                                            {opt.label}
                                          </option>
                                        ))}
                                    </select>
                                  );
                                }
                                return (
                                  <input
                                    className="studio-stage-select mono"
                                    placeholder="Model id"
                                    value={stage.model}
                                    readOnly={isWorkflowReadOnly}
                                    onChange={(e) => updateStage(index, { model: e.target.value })}
                                  />
                                );
                              })()}
                            </div>
                          </div>
                        ) : (
                          <div style={{ marginTop: 4 }}>
                            <div className="studio-stage-field-label" style={{ marginBottom: 8 }}>
                              Classification routes
                            </div>
                            {stage.classify_routes.map((route, routeIndex) => (
                              <div
                                key={routeIndex}
                                style={{ borderTop: "1px solid var(--bd)", paddingTop: 10, marginTop: 10 }}
                              >
                                <div className="studio-stage-fields two-col">
                                  <div>
                                    <div className="studio-stage-field-label">Languages</div>
                                    <select
                                      multiple
                                      className="studio-stage-select"
                                      style={{ minHeight: 72, height: "auto" }}
                                      value={route.languages}
                                      disabled={isWorkflowReadOnly}
                                      onChange={(e) =>
                                        updateRoute(index, routeIndex, {
                                          languages: Array.from(e.target.selectedOptions, (opt) => opt.value),
                                        })
                                      }
                                    >
                                      {LANGUAGE_OPTIONS.map((lang) => (
                                        <option key={lang} value={lang}>
                                          {lang}
                                        </option>
                                      ))}
                                    </select>
                                  </div>
                                  <div>
                                    <div className="studio-stage-field-label">Specialties</div>
                                    <select
                                      multiple
                                      className="studio-stage-select"
                                      style={{ minHeight: 72, height: "auto" }}
                                      value={route.specialties}
                                      disabled={isWorkflowReadOnly}
                                      onChange={(e) =>
                                        updateRoute(index, routeIndex, {
                                          specialties: Array.from(e.target.selectedOptions, (opt) => opt.value),
                                        })
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
                                <div
                                  style={{
                                    display: "grid",
                                    gridTemplateColumns: "1fr 1fr auto",
                                    gap: 10,
                                    marginTop: 8,
                                  }}
                                >
                                  <select
                                    className="studio-stage-select"
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
                                    className="studio-stage-select mono"
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
                                className="studio-add-stage-btn"
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

                        <label style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 10, fontSize: 12, color: "var(--txm)", cursor: "pointer", width: "fit-content" }}>
                          <input
                            type="checkbox"
                            checked={stage.gate_required}
                            disabled={isWorkflowReadOnly}
                            onChange={(e) => updateStage(index, { gate_required: e.target.checked })}
                            style={{ accentColor: "var(--ac)" }}
                          />
                          Require gate approval before leaving this stage
                        </label>
                      </div>
                    );
                  })}
                </div>
              </div>

              {!isWorkflowReadOnly && (
                <div className="studio-card-actions">
                  {selectedWorkflowSlug && (
                    <>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={deleteWorkflow.isPending}
                        onClick={() => deleteWorkflow.mutate(selectedWorkflowSlug)}
                      >
                        Delete
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={publishWorkflow.isPending}
                        onClick={() => publishWorkflow.mutate(selectedWorkflowSlug)}
                      >
                        {publishWorkflow.isPending ? "Publishing…" : "Publish to templates"}
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    className="btn-primary btn-cta"
                    disabled={!workflowDraft.name.trim() || saveWorkflow.isPending}
                    onClick={() => saveWorkflow.mutate()}
                  >
                    {saveWorkflow.isPending ? "Saving…" : selectedWorkflowSlug ? "Save workflow" : "Create workflow"}
                  </button>
                </div>
              )}
              </div>
            </div>

            <WorkflowPreviewPanel
              name={workflowDraft.name}
              slug={workflowDraft.slug}
              stages={workflowDraft.stages}
              agentLabel={(agentId) =>
                agentId ? agentOptions.find((opt) => opt.id === agentId)?.label ?? agentId : "Human"
              }
            />
          </div>
        )}
      </div>
    </div>
  );
}
