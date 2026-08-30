/**
 * The Stages card of the Studio's workflow editor.
 *
 * Lifted out of `StudioPage` verbatim. That file was 1673 lines against a
 * 1200-line gate, and the gate reads the whole staged file — so any edit to the
 * page, however small, was blocked behind a split. This card is the largest
 * self-contained thing in it: one visual card, five hundred lines, and the only
 * place the four stage-editing handlers below were used.
 *
 * ## What moved with it
 *
 * The handlers (`updateStage`, `updateRoute`, `updateParallelAgent`,
 * `removeParallelAgent`) had no other caller, so they belong here rather than
 * being passed down as four props. `stageTypeClass` and the two option lists
 * are likewise used nowhere else. What stayed shared — `emptyStage` and
 * `modelOptionsForAdapter`, which the page still calls — moved to
 * `studioWorkflowHelpers`, because importing them back out of the page would be
 * a cycle.
 *
 * ## What did not change
 *
 * The JSX. It is the same markup, dedented by two levels; the draft still lives
 * on the page, and every edit still goes through `setDraft`. A behavioural
 * change smuggled into a split is the thing a split is least able to show.
 */

import type { Dispatch, SetStateAction } from "react";

import type {
  ClassifyRoute,
  ParallelAgentSpec,
  RuntimeOptions,
  StudioAgent,
  StudioWorkflow,
  StudioWorkflowStage,
} from "../../api/client";
import { StageRouteHints } from "../StageRouteHints";
import { SkillSelect } from "./SkillSelect";
import {
  emptyStage,
  modelOptionsForAdapter,
  type StudioWorkflowDraft,
} from "./studioWorkflowHelpers";

const LANGUAGE_OPTIONS = ["python", "typescript", "javascript", "go", "rust", "java", "sql", "markdown"];
const SPECIALTY_OPTIONS = ["backend", "frontend", "testing", "planning", "research", "devops", "review"];

function stageTypeClass(type: StudioWorkflowStage["stage_type"]): string {
  if (type === "classify") return "classify";
  if (type === "gate") return "gate";
  if (type === "parallel") return "parallel";
  return "agent";
}

export interface StudioStagesCardProps {
  workflowDraft: StudioWorkflowDraft;
  setWorkflowDraft: Dispatch<SetStateAction<StudioWorkflowDraft>>;
  isWorkflowReadOnly: boolean;
  agentOptions: { id: string; label: string }[];
  agents: StudioAgent[];
  /** Skill names, as `/api/agents/skills` returns them. */
  skills: string[];
  runtimeOptions: RuntimeOptions | undefined;
  skipConditions: string[];
  /**
   * The saved workflow this draft came from, when there is one.
   *
   * Read only for its transitions, which the route hints draw: a draft's own
   * transitions are what the operator is editing, and the hints describe what
   * is currently *published*.
   */
  selectedWorkflow: StudioWorkflow | null;
}

export function StudioStagesCard({
  workflowDraft,
  setWorkflowDraft,
  isWorkflowReadOnly,
  agentOptions,
  agents,
  skills,
  runtimeOptions,
  skipConditions,
  selectedWorkflow,
}: StudioStagesCardProps) {
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

  const updateParallelAgent = (
    stageIndex: number,
    memberIndex: number,
    patch: Partial<ParallelAgentSpec>,
  ) => {
    setWorkflowDraft((draft) => ({
      ...draft,
      stages: draft.stages.map((stage, idx) => {
        if (idx !== stageIndex) return stage;
        const members = stage.parallel_agents.map((member, mIdx) =>
          mIdx === memberIndex ? { ...member, ...patch } : member,
        );
        return { ...stage, parallel_agents: members };
      }),
    }));
  };

  const removeParallelAgent = (stageIndex: number, memberIndex: number) => {
    setWorkflowDraft((draft) => ({
      ...draft,
      stages: draft.stages.map((stage, idx) =>
        idx === stageIndex
          ? { ...stage, parallel_agents: stage.parallel_agents.filter((_, mIdx) => mIdx !== memberIndex) }
          : stage,
      ),
    }));
  };

  return (
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
                  stage.stage_type === "classify"
                    ? "Classify"
                    : stage.stage_type === "gate"
                      ? "Gate"
                      : stage.stage_type === "parallel"
                        ? "Parallel"
                        : "Agent";
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
                                        to_stage: "",
                                      },
                                    ]
                                  : stage.classify_routes,
                              parallel_agents:
                                e.target.value === "parallel" && stage.parallel_agents.length === 0
                                  ? [
                                      { agent_id: "static_qa", skill_name: "static_qa" },
                                      { agent_id: "gatekeeper", skill_name: "ac_gate" },
                                    ]
                                  : stage.parallel_agents,
                            })
                          }
                        >
                          <option value="agent">Agent</option>
                          <option value="classify">Classify & route</option>
                          <option value="gate">Gate / review</option>
                          <option value="parallel">Parallel review</option>
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

                    {stage.stage_type === "classify" ? (
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
                              <SkillSelect
                                className="studio-stage-select mono"
                                value={route.skill_name}
                                disabled={isWorkflowReadOnly}
                                skills={skills}
                                onChange={(skillName: string) => updateRoute(index, routeIndex, { skill_name: skillName })}
                              />
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
                            <div style={{ marginTop: 8 }}>
                              <div className="studio-stage-field-label">Branches to</div>
                              <select
                                className="studio-stage-select mono"
                                value={route.to_stage ?? ""}
                                disabled={isWorkflowReadOnly}
                                onChange={(e) =>
                                  updateRoute(index, routeIndex, { to_stage: e.target.value })
                                }
                              >
                                <option value="">Continue to the next stage</option>
                                {workflowDraft.stages
                                  .filter((candidate) => candidate.key && candidate.key !== stage.key)
                                  .map((candidate) => (
                                    <option key={candidate.key} value={candidate.key}>
                                      {candidate.key}
                                    </option>
                                  ))}
                              </select>
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
                                    to_stage: "",
                                  },
                                ],
                              })
                            }
                          >
                            + Add route
                          </button>
                        )}
                      </div>
                    ) : stage.stage_type === "parallel" ? (
                      <div style={{ marginTop: 4 }}>
                        <div className="studio-stage-field-label" style={{ marginBottom: 8 }}>
                          Agents running in parallel
                        </div>
                        {stage.parallel_agents.map((member, memberIndex) => (
                          <div
                            key={memberIndex}
                            style={{
                              display: "grid",
                              gridTemplateColumns: "1fr 1fr auto",
                              gap: 10,
                              marginTop: memberIndex === 0 ? 0 : 10,
                              paddingTop: memberIndex === 0 ? 0 : 10,
                              borderTop: memberIndex === 0 ? undefined : "1px solid var(--bd)",
                            }}
                          >
                            <select
                              className="studio-stage-select"
                              value={member.agent_id}
                              disabled={isWorkflowReadOnly}
                              onChange={(e) => updateParallelAgent(index, memberIndex, { agent_id: e.target.value })}
                            >
                              {agentOptions.map((opt) => (
                                <option key={opt.id} value={opt.id}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                            <SkillSelect
                              className="studio-stage-select mono"
                              value={member.skill_name}
                              disabled={isWorkflowReadOnly}
                              skills={skills}
                              onChange={(skillName: string) => updateParallelAgent(index, memberIndex, { skill_name: skillName })}
                            />
                            {!isWorkflowReadOnly && (
                              <button
                                type="button"
                                className="studio-stage-remove"
                                aria-label={`Remove parallel agent ${memberIndex + 1}`}
                                onClick={() => removeParallelAgent(index, memberIndex)}
                              >
                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                                </svg>
                              </button>
                            )}
                          </div>
                        ))}
                        {!isWorkflowReadOnly && (
                          <button
                            type="button"
                            className="studio-add-stage-btn"
                            style={{ marginTop: 8 }}
                            onClick={() =>
                              updateStage(index, {
                                parallel_agents: [
                                  ...stage.parallel_agents,
                                  { agent_id: agentOptions[0]?.id ?? "", skill_name: "" },
                                ],
                              })
                            }
                          >
                            + Add agent
                          </button>
                        )}
                        <div style={{ marginTop: 8, fontSize: 11, color: "var(--txl)" }}>
                          All agents above run concurrently against the same ticket state; the stage only
                          advances once every one of them finishes.
                        </div>
                      </div>
                    ) : (
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
                          <SkillSelect
                            className="studio-stage-select mono"
                            value={stage.skill_name}
                            disabled={isWorkflowReadOnly}
                            skills={skills}
                            onChange={(skillName: string) => updateStage(index, { skill_name: skillName })}
                          />
                        </div>
                        <div>
                          <div className="studio-stage-field-label">Model override</div>
                          {(() => {
                            const stageAgent = agents?.find((a) => a.slug === stage.agent_id);
                            const modelOptions = modelOptionsForAdapter(
                              stageAgent?.adapter ?? "claude",
                              runtimeOptions,
                            );
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

                    <label style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 8, fontSize: 12, color: "var(--txm)", cursor: "pointer", width: "fit-content" }}>
                      <input
                        type="checkbox"
                        checked={Boolean(stage.terminal)}
                        disabled={isWorkflowReadOnly}
                        onChange={(e) => updateStage(index, { terminal: e.target.checked })}
                        style={{ accentColor: "var(--ac)" }}
                      />
                      Ends the workflow when reached
                    </label>

                    <div style={{ marginTop: 10 }}>
                      <div className="studio-stage-field-label">Skip this stage when</div>
                      <select
                        className="studio-stage-select mono"
                        value={stage.skip_when ?? ""}
                        disabled={isWorkflowReadOnly}
                        onChange={(e) => updateStage(index, { skip_when: e.target.value })}
                      >
                        <option value="">Never skip</option>
                        {skipConditions.map((condition) => (
                          <option key={condition} value={condition}>
                            {condition}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
  );
}
