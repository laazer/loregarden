/**
 * One agent's page in Agent Studio: a header that says how it has fared and
 * holds Save, then Overview (where it fails, recent runs, configuration),
 * Instructions, Runtime & tools, and the assembled Prompt.
 *
 * It owns the draft, the save/delete mutations and the live preview that used
 * to live in StudioPage beside three other studios' state. The route decides
 * which agent: `/studio/agents/<slug>` edits one, `/studio/agents/new` is a
 * duplicate being saved under a new id (New agent… itself is a dialog).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, type StudioAgent, type StudioWorkflow } from "../../api/client";
import { useRuntimeOptionsQuery } from "../../hooks/useRuntimeOptionsQuery";
import { ratePercent } from "../../lib/agentIndex";
import { recordTotals } from "../../lib/agentRunRecord";
import { studioPath } from "../../lib/appNavigation";
import { formatRelativeAge } from "../../lib/timestamps";
import {
  navigateToStudio,
  navigateToStudioAgent,
  navigateToStudioAgentNew,
  navigateToStudioWorkflow,
} from "../../lib/useAppNavigation";
import { AddToTabMenu } from "../AddToTabMenu";
import { Button } from "../ui/Button";
import { AgentDeleteConfirm } from "./AgentDeleteConfirm";
import { AgentDeleteRefusalNotice } from "./AgentDeleteRefusalNotice";
import { readAgentDeleteRefusal, resolveWorkflowDestination, type AgentDeleteRefusal } from "./agentDeleteRefusal";
import { agentUpdateBody, draftFromAgent, EMPTY_AGENT, type AgentDraft } from "./agentDraft";
import { AgentInstructionsTab, AgentRuntimeTab } from "./AgentEditorTabs";
import { AgentOverview, type AgentTab } from "./AgentOverview";
import { AgentPreviewContent } from "./AgentPreviewContent";
import { RecordTag, StudioEditorShell, type EditorTab } from "./StudioEditorShell";

const EXISTING_TABS: EditorTab<AgentTab>[] = [
  { id: "overview", label: "Overview" },
  { id: "instructions", label: "Instructions" },
  { id: "runtime", label: "Runtime & tools" },
  { id: "prompt", label: "Prompt" },
];
/** A duplicate not yet saved has no runs to show. */
const NEW_TABS = EXISTING_TABS.filter((tab) => tab.id !== "overview");

interface Props {
  /** The agent the route names, or null for an unsaved duplicate. */
  slug: string | null;
  agents: StudioAgent[];
  workflows: StudioWorkflow[];
}

export function AgentEditor({ slug, agents, workflows }: Props) {
  const qc = useQueryClient();
  const agent = useMemo(() => agents.find((item) => item.slug === slug) ?? null, [agents, slug]);
  // `read_only` is false for every DB row today (built-ins are editable and
  // versioned); kept so a locked agent still renders as a read view.
  const readOnly = Boolean(agent?.read_only);

  const [draft, setDraft] = useState<AgentDraft>({ ...EMPTY_AGENT });
  const [tab, setTab] = useState<AgentTab>(slug ? "overview" : "instructions");
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Set the moment the operator confirms, not when the mutation reports itself
  // pending: react-query flips `isPending` a microtask later, and an Escape
  // landing in that window would dismiss a dialog whose request is already away.
  const [deleteRequested, setDeleteRequested] = useState(false);
  // What the last delete refused over; it arrives on the rejection itself.
  const [deleteRefusal, setDeleteRefusal] = useState<AgentDeleteRefusal | null>(null);
  /** The draft as it stood when the delete was refused, so an edit clears the notice. */
  const refusalDraftRef = useRef<AgentDraft>(EMPTY_AGENT);

  const mcpGuides = useQuery({ queryKey: ["studio-mcp-tool-guides"], queryFn: api.studioMcpToolGuides });
  const studioDefaults = useQuery({ queryKey: ["studio-defaults"], queryFn: api.studioDefaults });
  const mcpServers = useQuery({ queryKey: ["mcp-servers"], queryFn: api.mcpServers });
  const skills = useQuery({ queryKey: ["agent-skills"], queryFn: api.skills });
  const runtimeOptions = useRuntimeOptionsQuery({ queryKey: ["runtime-options"] });
  const record = useQuery({
    queryKey: ["studio-agent-run-record", slug],
    queryFn: () => api.studioAgentRunRecord(slug!),
    enabled: Boolean(slug),
  });

  // Hydrate on a selection or a new saved version, not on every refetch of the
  // list (a new object each time), which would discard unsaved edits. An
  // unsaved duplicate keeps the draft it was seeded with.
  const agentRef = useRef(agent);
  agentRef.current = agent;
  const savedVersion = agent ? `${agent.slug}@${agent.updated_at}` : null;
  useEffect(() => {
    if (agentRef.current) setDraft(draftFromAgent(agentRef.current));
  }, [savedVersion]);

  // A different agent opens on its Overview; a duplicate opens on what to change.
  useEffect(() => {
    setTab(slug ? "overview" : "instructions");
    setDeleteRefusal(null);
    setConfirmDelete(false);
  }, [slug]);

  // The refusal stands until the operator acts: keyed on the draft's identity,
  // so a field added later cannot forget to clear it.
  useEffect(() => {
    if (deleteRefusal && draft !== refusalDraftRef.current) setDeleteRefusal(null);
  }, [draft, deleteRefusal]);

  const [previewPayload, setPreviewPayload] = useState<(Partial<StudioAgent> & { name: string }) | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(
      () => setPreviewPayload({ ...agentUpdateBody(draft), name: draft.name || "Preview Agent" }),
      350,
    );
    return () => window.clearTimeout(timer);
  }, [draft]);
  const preview = useQuery({
    queryKey: ["studio-agent-preview", previewPayload],
    queryFn: () => api.previewStudioAgent(previewPayload!),
    enabled: tab === "prompt" && Boolean(previewPayload?.name),
  });

  const dirty = useMemo(() => {
    if (!agent) return true;
    return JSON.stringify(agentUpdateBody(draft)) !== JSON.stringify(agentUpdateBody(draftFromAgent(agent)));
  }, [agent, draft]);

  const save = useMutation({
    meta: { errorTitle: "Save agent" },
    mutationFn: async () => {
      // Editing an existing agent updates it in place and records a version;
      // only a new slug creates. The update body carries no `slug` (fixed after
      // creation, and the schema forbids extra keys).
      const writable = agentUpdateBody(draft);
      if (agent) return api.updateStudioAgent(agent.slug, writable);
      return api.createStudioAgent({ ...writable, name: draft.name, slug: draft.slug || draft.name });
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["studio-agents"] });
      navigateToStudioAgent(saved.slug, true);
    },
  });

  const remove = useMutation({
    meta: { errorTitle: "Delete agent" },
    mutationFn: (target: string) => api.deleteStudioAgent(target),
    onSuccess: () => {
      setConfirmDelete(false);
      setDeleteRequested(false);
      qc.invalidateQueries({ queryKey: ["studio-agents"] });
      navigateToStudio("agents", true);
    },
    onError: (error) => {
      // The dialog comes down so the notice under it can be read; the toast
      // still fires from `meta.errorTitle`.
      setConfirmDelete(false);
      setDeleteRequested(false);
      refusalDraftRef.current = draft;
      setDeleteRefusal(readAgentDeleteRefusal(error));
    },
  });

  const duplicate = (source: StudioAgent) => {
    navigateToStudioAgentNew();
    setDraft({ ...draftFromAgent(source), slug: `${source.slug}-copy`, name: `${source.name} (copy)` });
  };

  const totals = record.data ? recordTotals(record.data) : null;
  const worst = record.data?.stages.find((stage) => stage.failed > 0);
  const summary = !slug ? (
    "Not saved yet: a copy under a new id. Change what you need, then create it."
  ) : totals ? (
    totals.runs ? (
      <>
        {totals.runs.toLocaleString()} finished runs · {totals.failed.toLocaleString()} failed
        {totals.rate !== null ? ` (${ratePercent(totals.rate)}%)` : ""}
        {totals.lastRunAt ? ` · last run ${formatRelativeAge(totals.lastRunAt)}` : ""}
        {worst ? ` · fails most at ${worst.stage_key || "no stage"} (${worst.failed.toLocaleString()})` : ""}
      </>
    ) : (
      "Has not run yet."
    )
  ) : null;

  const canSave = !readOnly && Boolean(draft.name.trim()) && dirty && !save.isPending;

  return (
    <StudioEditorShell
      backTo={studioPath("agents")}
      backLabel="Agents"
      title={agent?.name || draft.name || "New agent"}
      slug={agent?.slug ?? (draft.slug || undefined)}
      tags={
        <>
          {agent ? <RecordTag>{agent.built_in ? "built-in" : "custom"}</RecordTag> : null}
          {agent && dirty ? <RecordTag tone="warn">unsaved changes</RecordTag> : null}
        </>
      }
      summary={summary}
      actions={
        <>
          {agent ? (
            <AddToTabMenu
              primitiveId="chat_agent"
              values={new Map([["slug", agent.slug]])}
              title={agent.name || agent.slug}
              label={`Add ${agent.name || agent.slug} to a tab`}
            />
          ) : null}
          {agent ? (
            <Button variant="secondary" onClick={() => duplicate(agent)}>
              Duplicate
            </Button>
          ) : null}
          {agent && !agent.built_in && !readOnly ? (
            <Button
              variant="secondary"
              disabled={remove.isPending}
              onClick={(event) => {
                // Focused so the confirm's focus trap has an opener to return to.
                event.currentTarget.focus();
                setDeleteRequested(false);
                setConfirmDelete(true);
              }}
            >
              Delete
            </Button>
          ) : null}
          {!readOnly ? (
            <Button variant="primary" className="btn-cta" disabled={!canSave} onClick={() => save.mutate()}>
              {save.isPending ? "Saving…" : agent ? "Save agent" : "Create agent"}
            </Button>
          ) : null}
        </>
      }
      tabs={slug ? EXISTING_TABS : NEW_TABS}
      activeTab={tab}
      onTab={setTab}
    >
      <AgentDeleteRefusalNotice
        refusal={deleteRefusal}
        onOpenWorkflow={(target) => navigateToStudioWorkflow(target)}
        // A template id is not a Workflow Studio address; the draft that published it is.
        resolveWorkflowSlug={(kind, id) => resolveWorkflowDestination(kind, id, workflows)}
      />

      {tab === "overview" && agent ? <AgentOverview agent={agent} record={record} onEdit={setTab} /> : null}
      {tab === "instructions" ? (
        <AgentInstructionsTab draft={draft} setDraft={setDraft} readOnly={readOnly} slugFixed={Boolean(agent)} />
      ) : null}
      {tab === "runtime" ? (
        <AgentRuntimeTab
          draft={draft}
          setDraft={setDraft}
          readOnly={readOnly}
          agent={agent}
          skills={skills.data ?? []}
          runtimeOptions={runtimeOptions.data}
          mcpGuides={mcpGuides.data ?? []}
          mcpServers={mcpServers.data ?? []}
          defaults={studioDefaults.data}
        />
      ) : null}
      {tab === "prompt" ? (
        <AgentPreviewContent preview={preview.data} loading={preview.isFetching} slug={draft.slug || undefined} />
      ) : null}

      {agent ? (
        <AgentDeleteConfirm
          open={confirmDelete}
          slug={agent.slug}
          versionCount={agent.version ?? 0}
          pending={remove.isPending || deleteRequested}
          onConfirm={() => {
            setDeleteRequested(true);
            remove.mutate(agent.slug);
          }}
          onCancel={() => setConfirmDelete(false)}
        />
      ) : null}
    </StudioEditorShell>
  );
}
