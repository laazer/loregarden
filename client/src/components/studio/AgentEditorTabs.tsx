/**
 * The agent page's editing tabs. Instructions holds what the agent is told
 * (name, description, role); Runtime & tools holds how it runs and what it may
 * touch. The fields are the ones the old single-column editor stacked, now
 * split by the question each answers.
 */

import { useId } from "react";

import type {
  McpServerView,
  RuntimeOptions,
  StudioAgent,
  StudioDefaults,
  StudioMcpToolGuide,
} from "../../api/types";
import { Input } from "../ui/Input";
import { Select } from "../ui/Select";
import { Textarea } from "../ui/Textarea";
import type { AgentDraft } from "./agentDraft";
import { GateHandoffEditor } from "./GateHandoffEditor";
import { McpToolGuideSection } from "./McpToolGuideSection";
import { ADAPTERS } from "./studioAgentMeta";
import { modelOptionsForAdapter } from "./studioWorkflowHelpers";
import { ToolAccessSection } from "./ToolAccessSection";

interface DraftProps {
  draft: AgentDraft;
  setDraft: (draft: AgentDraft) => void;
  readOnly: boolean;
}

export function AgentInstructionsTab({
  draft,
  setDraft,
  readOnly,
  slugFixed,
}: DraftProps & { slugFixed: boolean }) {
  const fieldId = useId();
  return (
    <>
      <div className="studio-field-row">
        <div className="studio-field">
          <label htmlFor={`${fieldId}-name`} className="studio-field-label">
            Name
          </label>
          <Input
            id={`${fieldId}-name`}
            className="studio-input"
            value={draft.name}
            readOnly={readOnly}
            placeholder="e.g. Localization Reviewer"
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </div>
        <div className="studio-field">
          <label htmlFor={`${fieldId}-slug`} className="studio-field-label">
            Slug
          </label>
          {/* readOnly, not disabled: the slug is the id every workflow stage
              names, so it has to stay focusable and selectable to copy. */}
          <Input
            id={`${fieldId}-slug`}
            className="studio-input mono"
            value={draft.slug}
            placeholder="auto from name"
            readOnly={readOnly || slugFixed}
            aria-describedby={slugFixed ? `${fieldId}-slug-help` : undefined}
            onChange={(e) => setDraft({ ...draft, slug: e.target.value })}
          />
          {slugFixed && (
            <div id={`${fieldId}-slug-help`} className="studio-field-help">
              Fixed after creation — Duplicate makes a copy under a new id.
            </div>
          )}
        </div>
      </div>
      <div className="studio-field">
        <label htmlFor={`${fieldId}-description`} className="studio-field-label">
          Description
        </label>
        <Input
          id={`${fieldId}-description`}
          className="studio-input"
          value={draft.description}
          readOnly={readOnly}
          placeholder="One line — when should the orchestrator reach for this agent?"
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
        />
      </div>
      <div className="studio-field">
        <label htmlFor={`${fieldId}-role`} className="studio-field-label">
          Role instructions
        </label>
        <p id={`${fieldId}-role-hint`} className="studio-record-section-hint">
          What this agent does, its constraints and its output. This becomes the system prompt; the Prompt tab
          shows it assembled.
        </p>
        <Textarea
          id={`${fieldId}-role`}
          aria-describedby={`${fieldId}-role-hint`}
          className="studio-textarea agent-role-textarea"
          value={draft.role_body}
          readOnly={readOnly}
          onChange={(e) => setDraft({ ...draft, role_body: e.target.value })}
          placeholder="Review the staged diff against the ticket's acceptance criteria…"
        />
      </div>
    </>
  );
}

export function AgentRuntimeTab({
  draft,
  setDraft,
  readOnly,
  agent,
  skills,
  runtimeOptions,
  mcpGuides,
  mcpServers,
  defaults,
}: DraftProps & {
  agent: StudioAgent | null;
  skills: string[];
  runtimeOptions: RuntimeOptions | undefined;
  mcpGuides: StudioMcpToolGuide[];
  mcpServers: McpServerView[];
  defaults: StudioDefaults | undefined;
}) {
  const fieldId = useId();
  const modelOptions = modelOptionsForAdapter(draft.adapter, runtimeOptions);
  return (
    <>
      <section className="studio-record-section" aria-labelledby={`${fieldId}-runtime`}>
        <h3 id={`${fieldId}-runtime`} className="studio-record-section-title">
          Runtime
        </h3>
        <div className="studio-field-row agent-runtime-row">
          <div className="studio-field">
            <label htmlFor={`${fieldId}-provider`} className="studio-field-label">
              Provider
            </label>
            <Select
              id={`${fieldId}-provider`}
              className="studio-select"
              value={draft.adapter}
              disabled={readOnly}
              onChange={(e) => setDraft({ ...draft, adapter: e.target.value })}
            >
              {ADAPTERS.map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
          <div className="studio-field">
            <label htmlFor={`${fieldId}-model`} className="studio-field-label">
              Default model
            </label>
            {modelOptions ? (
              <Select
                id={`${fieldId}-model`}
                className="studio-select mono"
                value={draft.default_model}
                disabled={readOnly}
                onChange={(e) => setDraft({ ...draft, default_model: e.target.value })}
              >
                {modelOptions.map((opt) => (
                  <option key={opt.id || "default"} value={opt.id}>
                    {opt.label}
                  </option>
                ))}
              </Select>
            ) : (
              <Input
                id={`${fieldId}-model`}
                className="studio-input mono"
                placeholder="Model id"
                value={draft.default_model}
                readOnly={readOnly}
                onChange={(e) => setDraft({ ...draft, default_model: e.target.value })}
              />
            )}
          </div>
          <div className="studio-field">
            <label htmlFor={`${fieldId}-skill`} className="studio-field-label">
              Default skill
            </label>
            <Select
              id={`${fieldId}-skill`}
              className="studio-select mono"
              value={draft.default_skill}
              disabled={readOnly}
              onChange={(e) => setDraft({ ...draft, default_skill: e.target.value })}
            >
              <option value="">—</option>
              {skills.map((skill) => (
                <option key={skill} value={skill}>
                  {skill}
                </option>
              ))}
            </Select>
          </div>
          <div className="studio-field">
            <label htmlFor={`${fieldId}-timeout`} className="studio-field-label">
              Timeout (s)
            </label>
            <Input
              id={`${fieldId}-timeout`}
              type="number"
              className="studio-input mono"
              value={draft.timeout}
              readOnly={readOnly}
              onChange={(e) => setDraft({ ...draft, timeout: Number(e.target.value) || 600 })}
            />
          </div>
        </div>
      </section>

      {readOnly ? (
        <section className="studio-record-section" aria-labelledby={`${fieldId}-tools`}>
          <h3 id={`${fieldId}-tools`} className="studio-record-section-title">
            MCP tools
          </h3>
          {draft.mcp_enabled && draft.mcp_tools.length ? (
            <div className="agent-tool-chips">
              {draft.mcp_tools.map((tool) => (
                <span key={tool} className="studio-preview-chip">
                  {tool}
                </span>
              ))}
            </div>
          ) : (
            <p className="studio-record-muted">No MCP tools.</p>
          )}
        </section>
      ) : (
        <>
          <McpToolGuideSection
            variant="studio"
            guides={mcpGuides}
            enabled={draft.mcp_enabled}
            selected={draft.mcp_tools}
            onToggleEnabled={(enabled) =>
              setDraft({
                ...draft,
                mcp_enabled: enabled,
                mcp_tools: enabled ? defaults?.mcp_tools ?? draft.mcp_tools : [],
              })
            }
            onToggleTool={(tool) =>
              setDraft({
                ...draft,
                mcp_tools: draft.mcp_tools.includes(tool)
                  ? draft.mcp_tools.filter((item) => item !== tool)
                  : [...draft.mcp_tools, tool],
              })
            }
          />
          <ToolAccessSection
            grants={draft.tool_grants}
            warnings={agent?.tool_grant_warnings ?? []}
            servers={mcpServers.filter((s) => s.enabled).map((s) => s.name)}
            onChange={(tool_grants) => setDraft({ ...draft, tool_grants })}
          />
          <div className="studio-card">
            <GateHandoffEditor
              gateChecks={draft.gate_checks}
              handoffChecks={draft.handoff_checks}
              onChange={(gate_checks, handoff_checks) => setDraft({ ...draft, gate_checks, handoff_checks })}
            />
          </div>
        </>
      )}
    </>
  );
}
