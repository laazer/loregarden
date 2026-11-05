/**
 * One workflow's page in Workflow Studio, on the same shell as an agent's: a
 * header that says how much it is used and holds Save and Publish, then
 * Overview (its pipeline and usage), Stages, and Details.
 *
 * It owns the draft and the save/publish/delete mutations that used to live in
 * StudioPage. `/studio/workflows/<slug>` edits one; `/studio/workflows/new` is
 * a duplicate being saved under a new id (New workflow… itself is a dialog).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { api, type StudioAgent, type StudioWorkflow } from "../../api/client";
import { useRuntimeOptionsQuery } from "../../hooks/useRuntimeOptionsQuery";
import { studioPath } from "../../lib/appNavigation";
import { formatRelativeAge } from "../../lib/timestamps";
import { navigateToStudio, navigateToStudioWorkflow, navigateToStudioWorkflowNew } from "../../lib/useAppNavigation";
import { workflowIndexRows } from "../../lib/workflowIndex";
import { errorStatus } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { notifyStrippedSkills } from "./skillOptions";
import { StudioStagesCard } from "./StudioStagesCard";
import { RecordTag, StudioEditorShell, type EditorTab } from "./StudioEditorShell";
import { draftFromWorkflow, emptyWorkflowDraft, type WorkflowDraft } from "./workflowDraft";
import { WorkflowDriftNotice } from "./WorkflowDriftNotice";
import { WorkflowPreviewPanel } from "./WorkflowPreviewPanel";

type WorkflowTab = "overview" | "stages" | "details";

const EXISTING_TABS: EditorTab<WorkflowTab>[] = [
  { id: "overview", label: "Overview" },
  { id: "stages", label: "Stages" },
  { id: "details", label: "Details" },
];
const NEW_TABS: EditorTab<WorkflowTab>[] = [
  { id: "details", label: "Details" },
  { id: "stages", label: "Stages" },
];

interface Props {
  /** The workflow the route names, or null for an unsaved duplicate. */
  slug: string | null;
  workflows: StudioWorkflow[];
  agents: StudioAgent[];
}

export function WorkflowEditor({ slug, workflows, agents }: Props) {
  const fieldId = useId();
  const qc = useQueryClient();
  const workflow = useMemo(() => workflows.find((item) => item.slug === slug) ?? null, [workflows, slug]);
  // Built-in templates are edited through the draft → publish flow, not inline.
  const readOnly = Boolean(workflow?.read_only || workflow?.built_in);

  const [draft, setDraft] = useState<WorkflowDraft>(emptyWorkflowDraft);
  const [tab, setTab] = useState<WorkflowTab>(slug ? "overview" : "details");

  const usage = useQuery({ queryKey: ["studio-workflow-usage"], queryFn: api.studioWorkflowUsage });
  const skills = useQuery({ queryKey: ["agent-skills"], queryFn: api.skills });
  const exitActionRequirements = useQuery({
    queryKey: ["studio-exit-action-requirements"],
    queryFn: api.studioExitActionRequirements,
  });
  const studioDefaults = useQuery({ queryKey: ["studio-defaults"], queryFn: api.studioDefaults });
  const runtimeOptions = useRuntimeOptionsQuery({ queryKey: ["runtime-options"] });

  // Hydrate on a selection or a new saved version, not on every refetch.
  const workflowRef = useRef(workflow);
  workflowRef.current = workflow;
  const savedVersion = workflow ? `${workflow.slug}@${workflow.updated_at}` : null;
  useEffect(() => {
    if (workflowRef.current) setDraft(draftFromWorkflow(workflowRef.current));
  }, [savedVersion]);

  useEffect(() => {
    setTab(slug ? "overview" : "details");
  }, [slug]);

  const agentOptions = useMemo(() => agents.map((a) => ({ id: a.slug, label: a.name })), [agents]);
  const agentLabel = (agentId: string) =>
    agentId ? agentOptions.find((opt) => opt.id === agentId)?.label ?? agentId : "Human";

  const dirty = useMemo(() => {
    if (!workflow) return true;
    return JSON.stringify(draft) !== JSON.stringify(draftFromWorkflow(workflow));
  }, [workflow, draft]);

  const save = useMutation({
    meta: { errorTitle: "Save workflow" },
    mutationFn: async () => {
      const { transitions, ...rest } = draft;
      const payload = {
        ...rest,
        slug: draft.slug || draft.name,
        stages: draft.stages.map((stage, idx) => ({ ...stage, order: idx + 1 })),
        // Omit rather than send []: the server reads an explicit empty array as
        // "delete every route". Omitting preserves what is there (or seeds a
        // linear chain for a workflow with none).
        ...(transitions.length ? { transitions } : {}),
      };
      if (workflow && !readOnly) return api.updateStudioWorkflow(workflow.slug, payload);
      return api.createStudioWorkflow(payload);
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["studio-workflows"] });
      qc.invalidateQueries({ queryKey: ["workflow-templates"] });
      navigateToStudioWorkflow(saved.slug, true);
    },
  });

  const publish = useMutation({
    meta: { errorTitle: "Publish workflow" },
    // A 409 means the publish would strand live tickets on a stage it removes:
    // a confirmation, not an error. Re-issuing with confirm proceeds.
    mutationFn: ({ target, confirm }: { target: string; confirm: boolean }) =>
      api.publishStudioWorkflow(target, confirm),
    onSuccess: (published) => {
      notifyStrippedSkills("Workflow published", published);
      qc.invalidateQueries({ queryKey: ["studio-workflows"] });
      qc.invalidateQueries({ queryKey: ["workflow-templates"] });
      qc.invalidateQueries({ queryKey: ["studio-workflow-drift"] });
    },
  });
  // Only a 409 offers the override; every other failure has no valid "anyway".
  const publishNeedsConfirmation = errorStatus(publish.error) === 409;

  const remove = useMutation({
    meta: { errorTitle: "Delete workflow" },
    mutationFn: (target: string) => api.deleteStudioWorkflow(target),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["studio-workflows"] });
      navigateToStudio("workflows", true);
    },
  });

  const duplicate = (source: StudioWorkflow) => {
    navigateToStudioWorkflowNew();
    setDraft({
      ...draftFromWorkflow(source),
      slug: `${source.slug}-copy`,
      name: `${source.name} (copy)`,
      stages: source.stages.map((stage, idx) => ({ ...stage, order: idx + 1 })),
      // Carry the source's transitions: dropping them would wipe its `when:
      // reject` edges on the copy's first save.
      transitions: source.transitions ?? [],
    });
  };

  const row = workflow && usage.data ? workflowIndexRows([workflow], usage.data)[0] : null;
  const summary = !slug
    ? "Not saved yet: a copy under a new id. Change what you need, then create it."
    : row
      ? [
          `${draft.stages.length} stage${draft.stages.length === 1 ? "" : "s"}`,
          row.tickets
            ? `${row.tickets.toLocaleString()} ticket${row.tickets === 1 ? "" : "s"} · ${row.open.toLocaleString()} open`
            : "no tickets yet",
          row.lastUsedAt ? `last used ${formatRelativeAge(row.lastUsedAt)}` : null,
        ]
          .filter(Boolean)
          .join(" · ")
      : `${draft.stages.length} stage${draft.stages.length === 1 ? "" : "s"}`;

  const canSave = !readOnly && Boolean(draft.name.trim()) && dirty && !save.isPending;

  return (
    <StudioEditorShell
      backTo={studioPath("workflows")}
      backLabel="Workflows"
      title={workflow?.name || draft.name || "New workflow"}
      slug={workflow ? workflow.published_template_slug || workflow.slug : draft.slug || undefined}
      tags={
        <>
          {row ? <RecordTag>{row.kind}</RecordTag> : null}
          {workflow && !readOnly && dirty ? <RecordTag tone="warn">unsaved changes</RecordTag> : null}
        </>
      }
      summary={summary}
      actions={
        <>
          {workflow ? (
            <Button variant="secondary" onClick={() => duplicate(workflow)}>
              Duplicate
            </Button>
          ) : null}
          {workflow && !readOnly ? (
            <>
              <Button variant="secondary" disabled={remove.isPending} onClick={() => remove.mutate(workflow.slug)}>
                Delete
              </Button>
              <Button
                variant="secondary"
                disabled={publish.isPending}
                onClick={() => publish.mutate({ target: workflow.slug, confirm: false })}
              >
                {publish.isPending ? "Publishing…" : "Publish to templates"}
              </Button>
              {publishNeedsConfirmation ? (
                <Button
                  variant="secondary"
                  disabled={publish.isPending}
                  onClick={() => publish.mutate({ target: workflow.slug, confirm: true })}
                >
                  Publish anyway
                </Button>
              ) : null}
            </>
          ) : null}
          {!readOnly ? (
            <Button variant="primary" className="btn-cta" disabled={!canSave} onClick={() => save.mutate()}>
              {save.isPending ? "Saving…" : workflow ? "Save workflow" : "Create workflow"}
            </Button>
          ) : null}
        </>
      }
      tabs={slug ? EXISTING_TABS : NEW_TABS}
      activeTab={tab}
      onTab={setTab}
    >
      {workflow && !readOnly ? <WorkflowDriftNotice slug={workflow.slug} /> : null}

      {tab === "overview" ? (
        <>
          {draft.description ? <p className="studio-record-description">{draft.description}</p> : null}
          {readOnly ? (
            <p className="studio-record-section-hint">
              Built-in: Duplicate makes an editable copy.
              {workflow?.source_path ? ` Defined in ${workflow.source_path}.` : ""}
            </p>
          ) : null}
          <section className="studio-record-section" aria-labelledby={`${fieldId}-pipeline`}>
            <h3 id={`${fieldId}-pipeline`} className="studio-record-section-title">
              Pipeline
            </h3>
            <WorkflowPreviewPanel
              inline
              name={draft.name}
              slug={draft.slug}
              stages={draft.stages}
              agentLabel={agentLabel}
            />
          </section>
        </>
      ) : null}

      {tab === "stages" ? (
        <StudioStagesCard
          workflowDraft={draft}
          setWorkflowDraft={setDraft}
          isWorkflowReadOnly={readOnly}
          agentOptions={agentOptions}
          agents={agents}
          skills={skills.data ?? []}
          exitActionRequirements={exitActionRequirements.data}
          runtimeOptions={runtimeOptions.data}
          skipConditions={studioDefaults.data?.skip_conditions ?? []}
          selectedWorkflow={workflow}
        />
      ) : null}

      {tab === "details" ? (
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
                placeholder="e.g. Hotfix express"
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </div>
            <div className="studio-field">
              <label htmlFor={`${fieldId}-slug`} className="studio-field-label">
                Slug
              </label>
              <Input
                id={`${fieldId}-slug`}
                className="studio-input mono"
                value={draft.slug}
                placeholder="auto from name"
                readOnly={readOnly}
                onChange={(e) => setDraft({ ...draft, slug: e.target.value })}
              />
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
              placeholder="What kind of ticket runs on this workflow, and why."
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            />
          </div>
        </>
      ) : null}
    </StudioEditorShell>
  );
}
