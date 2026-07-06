import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  api,
  type RuntimeOptions,
  type TicketStudioDraftItem,
  type TicketStudioSession,
  type WorkspaceRuntimeSettings,
  type WorkspaceSummary,
} from "../../api/client";
import { ParentTicketSelector } from "../ParentTicketSelector";
import { workItemTypeLabel } from "../../lib/workItemHierarchy";
import { runtimeSummaryLabel } from "../WorkspaceRuntimeFields";
import { TriageModelModal } from "../TriageModelModal";
import { ChatComposer } from "../chat/ChatComposer";
import { ChatWindow } from "../chat/ChatWindow";
import { TicketStudioDraftModal } from "./TicketStudioDraftModal";

const DEFAULT_RUNTIME: WorkspaceRuntimeSettings = {
  cli_adapter: "default",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};

function draftSummaryLine(item: TicketStudioDraftItem): string {
  const parts: string[] = [];
  if (item.parent_ref) parts.push(`parent: ${item.parent_ref}`);
  if (item.acceptance_criteria.length > 0) {
    parts.push(`${item.acceptance_criteria.length} AC`);
  }
  if (item.priority !== 3) parts.push(`P${item.priority}`);
  if (item.suggested_agent) parts.push(item.suggested_agent);
  return parts.join(" · ");
}

function emptySessionDraft(): { title: string; brief: string; parent_ticket_id: string } {
  return { title: "", brief: "", parent_ticket_id: "" };
}

export function TicketStudioPanel({
  workspaces,
  runtimeOptions,
}: {
  workspaces: WorkspaceSummary[];
  runtimeOptions: RuntimeOptions | undefined;
}) {
  const qc = useQueryClient();
  const [workspaceSlug, setWorkspaceSlug] = useState(workspaces[0]?.slug ?? "loregarden");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [newDraft, setNewDraft] = useState(emptySessionDraft);
  const [chatDraft, setChatDraft] = useState("");
  const [modelModalOpen, setModelModalOpen] = useState(false);
  const [localDraft, setLocalDraft] = useState<TicketStudioDraftItem[]>([]);
  const [draftDirty, setDraftDirty] = useState(false);
  const [answerDraft, setAnswerDraft] = useState<string[]>([]);
  const [expandedDraftIndex, setExpandedDraftIndex] = useState<number | null>(null);

  const sessions = useQuery({
    queryKey: ["ticket-studio-sessions", workspaceSlug],
    queryFn: () => api.ticketStudioSessions(workspaceSlug),
    enabled: !!workspaceSlug,
  });

  const studioAgents = useQuery({
    queryKey: ["studio-agents"],
    queryFn: api.studioAgents,
  });

  const selectedSession = useMemo(
    () => sessions.data?.find((s) => s.id === selectedSessionId) ?? null,
    [sessions.data, selectedSessionId],
  );

  useEffect(() => {
    if (!selectedSession) {
      setLocalDraft([]);
      setDraftDirty(false);
      setAnswerDraft([]);
      return;
    }
    setLocalDraft(selectedSession.draft);
    setDraftDirty(false);
    setAnswerDraft(selectedSession.clarifying_answers);
    setExpandedDraftIndex(null);
  }, [selectedSession?.id, selectedSession?.draft, selectedSession?.updated_at, selectedSession?.clarifying_answers]);

  const requestClarifications = useMutation({
    mutationFn: () => api.requestTicketStudioClarifications(selectedSessionId!),
    onSuccess: (updated) => {
      qc.setQueryData(["ticket-studio-sessions", workspaceSlug], (current: TicketStudioSession[] | undefined) =>
        current ? current.map((s) => (s.id === updated.id ? updated : s)) : [updated],
      );
      setAnswerDraft(updated.clarifying_answers);
    },
  });

  const saveClarifications = useMutation({
    mutationFn: () => api.saveTicketStudioClarifications(selectedSessionId!, answerDraft),
    onSuccess: (updated) => {
      qc.setQueryData(["ticket-studio-sessions", workspaceSlug], (current: TicketStudioSession[] | undefined) =>
        current ? current.map((s) => (s.id === updated.id ? updated : s)) : [updated],
      );
      setAnswerDraft(updated.clarifying_answers);
    },
  });

  const createSession = useMutation({
    mutationFn: async () => {
      const created = await api.createTicketStudioSession({
        workspace_slug: workspaceSlug,
        title: newDraft.title.trim(),
        brief: newDraft.brief.trim(),
        parent_ticket_id: newDraft.parent_ticket_id || null,
      });
      try {
        const clarified = await api.requestTicketStudioClarifications(created.id);
        if (clarified.clarifying_resolved && clarified.clarifying_questions.length === 0) {
          return api.generateTicketStudioScope(clarified.id);
        }
        return clarified;
      } catch {
        return created;
      }
    },
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ["ticket-studio-sessions", workspaceSlug] });
      qc.setQueryData(["ticket-studio-sessions", workspaceSlug], (current: TicketStudioSession[] | undefined) =>
        current ? current.map((s) => (s.id === updated.id ? updated : s)) : [updated],
      );
      setSelectedSessionId(updated.id);
      setAnswerDraft(updated.clarifying_answers);
      setNewDraft(emptySessionDraft());
    },
  });

  const sendMessage = useMutation({
    mutationFn: (content: string) => api.sendTicketStudioMessage(selectedSessionId!, content),
    onSuccess: (updated) => {
      qc.setQueryData(["ticket-studio-sessions", workspaceSlug], (current: TicketStudioSession[] | undefined) =>
        current ? current.map((s) => (s.id === updated.id ? updated : s)) : [updated],
      );
      setChatDraft("");
    },
  });

  const generateScope = useMutation({
    mutationFn: () => api.generateTicketStudioScope(selectedSessionId!),
    onSuccess: (updated) => {
      qc.setQueryData(["ticket-studio-sessions", workspaceSlug], (current: TicketStudioSession[] | undefined) =>
        current ? current.map((s) => (s.id === updated.id ? updated : s)) : [updated],
      );
    },
  });

  const saveDraft = useMutation({
    mutationFn: () => api.updateTicketStudioDraft(selectedSessionId!, localDraft),
    onSuccess: (updated) => {
      qc.setQueryData(["ticket-studio-sessions", workspaceSlug], (current: TicketStudioSession[] | undefined) =>
        current ? current.map((s) => (s.id === updated.id ? updated : s)) : [updated],
      );
      setDraftDirty(false);
    },
  });

  const commitSession = useMutation({
    mutationFn: () => api.commitTicketStudioSession(selectedSessionId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-studio-sessions", workspaceSlug] });
      qc.invalidateQueries({ queryKey: ["tickets", workspaceSlug] });
      qc.invalidateQueries({ queryKey: ["ticket-tree", workspaceSlug] });
    },
  });

  const deleteSession = useMutation({
    mutationFn: (id: string) => api.deleteTicketStudioSession(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-studio-sessions", workspaceSlug] });
      setSelectedSessionId(null);
    },
  });

  const saveRuntime = useMutation({
    mutationFn: (runtime: WorkspaceRuntimeSettings) =>
      api.setTicketStudioRuntime(selectedSessionId!, runtime),
    onSuccess: (saved) => {
      qc.setQueryData(["ticket-studio-sessions", workspaceSlug], (current: TicketStudioSession[] | undefined) =>
        current
          ? current.map((s) => (s.id === selectedSessionId ? { ...s, runtime: saved } : s))
          : current,
      );
    },
  });

  const isReadOnly = selectedSession?.status === "committed";
  const selectedCount = localDraft.filter((item) => item.selected).length;
  const runtime = selectedSession?.runtime ?? DEFAULT_RUNTIME;
  const modelLabel = runtimeSummaryLabel(runtime, runtimeOptions);
  const hasOpenQuestions = (selectedSession?.clarifying_questions.length ?? 0) > 0;
  const clarifyingResolved = selectedSession?.clarifying_resolved ?? true;
  const answersDirty =
    hasOpenQuestions &&
    answerDraft.some(
      (answer, index) => answer.trim() !== (selectedSession?.clarifying_answers[index] ?? "").trim(),
    );
  const answersComplete =
    !hasOpenQuestions ||
    (selectedSession?.clarifying_questions.every((_, index) => answerDraft[index]?.trim()) ?? false);
  const isScoperThinking =
    sendMessage.isPending || requestClarifications.isPending || generateScope.isPending || createSession.isPending;

  const updateDraftItem = (index: number, patch: Partial<TicketStudioDraftItem>) => {
    setLocalDraft((items) => items.map((item, idx) => (idx === index ? { ...item, ...patch } : item)));
    setDraftDirty(true);
  };

  const expandedDraftItem =
    expandedDraftIndex != null ? (localDraft[expandedDraftIndex] ?? null) : null;

  return (
    <>
      <aside
        style={{
          width: 260,
          borderRight: "1px solid var(--bd)",
          background: "var(--bg0)",
          padding: 12,
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <div className="modal-field">
          <div className="modal-field-label">Workspace</div>
          <select
            className="btn-secondary filter-select"
            style={{ width: "100%" }}
            value={workspaceSlug}
            onChange={(e) => {
              setWorkspaceSlug(e.target.value);
              setSelectedSessionId(null);
            }}
          >
            {workspaces.map((ws) => (
              <option key={ws.slug} value={ws.slug}>
                {ws.name}
              </option>
            ))}
          </select>
        </div>

        <div className="state-label">Sessions</div>
        <button
          type="button"
          className="btn-primary"
          style={{ width: "100%" }}
          onClick={() => {
            setSelectedSessionId(null);
            setNewDraft(emptySessionDraft());
          }}
        >
          + New scope
        </button>

        {(sessions.data ?? []).map((session) => (
          <button
            key={session.id}
            type="button"
            className={`list-btn ${selectedSessionId === session.id ? "active" : ""}`}
            onClick={() => setSelectedSessionId(session.id)}
            style={{ marginBottom: 6, textAlign: "left" }}
          >
            <div style={{ fontWeight: 600 }}>{session.title}</div>
            <div style={{ fontSize: 10.5, color: "var(--txl)" }}>
              {session.status} · {session.draft.length} draft
            </div>
          </button>
        ))}
      </aside>

      <main style={{ flex: 1, overflow: "auto", padding: 20, minWidth: 0 }}>
        {!selectedSessionId ? (
          <div style={{ maxWidth: 720 }}>
            <h2 style={{ margin: "0 0 6px", fontFamily: "var(--dp)" }}>New feature scope</h2>
            <p className="modal-hint" style={{ marginTop: 0, marginBottom: 16 }}>
              Describe a feature and let the Planner agent decompose it into milestones, capabilities, and tasks.
            </p>

            <div className="modal-field">
              <div className="modal-field-label">Feature title</div>
              <input
                className="btn-secondary"
                style={{ width: "100%", boxSizing: "border-box" }}
                value={newDraft.title}
                onChange={(e) => setNewDraft({ ...newDraft, title: e.target.value })}
                placeholder="e.g. Ticket Studio"
              />
            </div>

            <ParentTicketSelector
              workspaceSlug={workspaceSlug}
              value={newDraft.parent_ticket_id || null}
              onChange={(parentId) =>
                setNewDraft((draft) => ({ ...draft, parent_ticket_id: parentId ?? "" }))
              }
              allowNone
              noneLabel="None (root feature or milestone)"
              label="Optional parent"
              hint="Scope under an existing milestone, feature, or capability."
            />

            <div className="modal-field">
              <div className="modal-field-label">Feature brief</div>
              <textarea
                className="btn-secondary"
                style={{ width: "100%", minHeight: 160, boxSizing: "border-box", fontSize: 12.5 }}
                value={newDraft.brief}
                onChange={(e) => setNewDraft({ ...newDraft, brief: e.target.value })}
                placeholder="Problem, users, constraints, success metrics, technical notes…"
              />
            </div>

            <button
              type="button"
              className="btn-primary"
              disabled={!newDraft.title.trim() || createSession.isPending}
              onClick={() => createSession.mutate()}
            >
              {createSession.isPending ? "Starting scope…" : "Start scoping session"}
            </button>
            {createSession.isError && (
              <p className="modal-hint" style={{ color: "var(--rdl)", marginTop: 8 }}>
                {(createSession.error as Error).message}
              </p>
            )}
          </div>
        ) : selectedSession ? (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, alignItems: "start" }}>
            <section style={{ minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
                <div>
                  <h2 style={{ margin: "0 0 4px", fontFamily: "var(--dp)" }}>{selectedSession.title}</h2>
                  <p className="modal-hint" style={{ margin: 0 }}>
                    {isReadOnly ? "Committed to workspace" : "Chat with the scoper to refine scope"}
                    {selectedSession.parent_ticket_title
                      ? ` · under ${selectedSession.parent_ticket_title}`
                      : ""}
                  </p>
                </div>
                {!isReadOnly && (
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={deleteSession.isPending}
                    onClick={() => deleteSession.mutate(selectedSession.id)}
                  >
                    Delete
                  </button>
                )}
              </div>

              {selectedSession.summary && (
                <div className="state-card" style={{ marginTop: 12, fontSize: 12.5 }}>
                  {selectedSession.summary}
                </div>
              )}

              {hasOpenQuestions && !isReadOnly && (
                <div className="state-card" style={{ marginTop: 12, padding: 12 }}>
                  <div className="modal-section-title" style={{ marginTop: 0 }}>
                    Clarifying questions
                  </div>
                  <p className="modal-hint" style={{ margin: "0 0 10px" }}>
                    Answer these before generating tickets.
                  </p>
                  {selectedSession.clarifying_questions.map((question, index) => (
                    <div key={question} className="modal-field" style={{ marginBottom: 10 }}>
                      <div className="modal-field-label">{question}</div>
                      <textarea
                        className="btn-secondary"
                        style={{ width: "100%", minHeight: 56, boxSizing: "border-box", fontSize: 12 }}
                        value={answerDraft[index] ?? ""}
                        onChange={(e) =>
                          setAnswerDraft((current) => {
                            const next = [...current];
                            next[index] = e.target.value;
                            return next;
                          })
                        }
                      />
                    </div>
                  ))}
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={!answersComplete || saveClarifications.isPending || !answersDirty}
                    onClick={() => saveClarifications.mutate()}
                  >
                    {saveClarifications.isPending ? "Saving…" : "Save answers"}
                  </button>
                  {!clarifyingResolved && (
                    <p className="modal-hint" style={{ margin: "8px 0 0", color: "var(--amb)" }}>
                      Save your answers to unlock ticket generation.
                    </p>
                  )}
                </div>
              )}

              {hasOpenQuestions && isReadOnly && (
                <div style={{ marginTop: 12 }}>
                  <div className="modal-section-title">Clarifying questions</div>
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12.5, color: "var(--txm)" }}>
                    {selectedSession.clarifying_questions.map((q, index) => (
                      <li key={q}>
                        <div>{q}</div>
                        {selectedSession.clarifying_answers[index] && (
                          <div style={{ color: "var(--txl)", marginTop: 2 }}>{selectedSession.clarifying_answers[index]}</div>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div style={{ marginTop: 14, display: "flex", flexDirection: "column", minHeight: 0 }}>
                <ChatWindow
                  title="Scope chat"
                  messages={selectedSession.messages}
                  assistantLabel="Scoper"
                  emptyMessage="Review the brief for questions, then generate tickets."
                  isThinking={isScoperThinking}
                  thinkingMessage="Scoper is thinking…"
                  maxHeight={280}
                />
                {!isReadOnly && (
                  <ChatComposer
                    value={chatDraft}
                    onChange={setChatDraft}
                    onSubmit={() => sendMessage.mutate(chatDraft.trim())}
                    placeholder="Ask to refine scope, split work, or tighten acceptance criteria…"
                    isSending={sendMessage.isPending}
                    disabled={isScoperThinking}
                    actions={
                      <>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={!runtimeOptions}
                          onClick={() => setModelModalOpen(true)}
                        >
                          Model · {modelLabel}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={isScoperThinking}
                          onClick={() => requestClarifications.mutate()}
                        >
                          {requestClarifications.isPending ? "Reviewing…" : "Review brief"}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={!clarifyingResolved || isScoperThinking}
                          onClick={() => generateScope.mutate()}
                          title={!clarifyingResolved ? "Answer clarifying questions first" : undefined}
                        >
                          {generateScope.isPending ? "Generating…" : "Generate tickets"}
                        </button>
                      </>
                    }
                  />
                )}
              </div>
            </section>

            <section style={{ minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <div className="modal-section-title" style={{ margin: 0 }}>
                  Draft tickets ({selectedCount} selected)
                </div>
                {!isReadOnly && draftDirty && (
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={saveDraft.isPending}
                    onClick={() => saveDraft.mutate()}
                  >
                    Save edits
                  </button>
                )}
              </div>

              {localDraft.length === 0 ? (
                <p className="modal-hint">
                  {hasOpenQuestions && !clarifyingResolved
                    ? "Answer clarifying questions, then generate tickets."
                    : "Review the brief or generate tickets to populate the draft."}
                </p>
              ) : (
                localDraft.map((item, index) => {
                  const summary = draftSummaryLine(item);
                  return (
                    <div key={item.ref} className="state-card" style={{ marginBottom: 10, padding: 10 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <input
                          type="checkbox"
                          checked={item.selected}
                          disabled={isReadOnly}
                          onChange={(e) => updateDraftItem(index, { selected: e.target.checked })}
                          aria-label={`Include ${item.title}`}
                        />
                        <span
                          style={{
                            fontSize: 10.5,
                            fontWeight: 600,
                            color: "var(--txl)",
                            textTransform: "uppercase",
                            letterSpacing: "0.04em",
                            flexShrink: 0,
                          }}
                        >
                          {workItemTypeLabel(item.work_item_type)}
                        </span>
                        <button
                          type="button"
                          className="list-btn"
                          style={{
                            flex: 1,
                            minWidth: 0,
                            textAlign: "left",
                            padding: "4px 8px",
                            fontWeight: 600,
                          }}
                          onClick={() => setExpandedDraftIndex(index)}
                        >
                          <span
                            style={{
                              display: "block",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {item.title}
                          </span>
                          {summary && (
                            <span
                              style={{
                                display: "block",
                                fontSize: 10.5,
                                fontWeight: 400,
                                color: "var(--txl)",
                                marginTop: 2,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {summary}
                            </span>
                          )}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary btn-compact"
                          onClick={() => setExpandedDraftIndex(index)}
                        >
                          Details
                        </button>
                      </div>
                      {item.description && (
                        <p
                          style={{
                            margin: "8px 0 0",
                            fontSize: 11.5,
                            color: "var(--txm)",
                            overflow: "hidden",
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                          }}
                        >
                          {item.description}
                        </p>
                      )}
                    </div>
                  );
                })
              )}

              {!isReadOnly && localDraft.length > 0 && (
                <button
                  type="button"
                  className="btn-primary"
                  style={{ marginTop: 8 }}
                  disabled={selectedCount === 0 || commitSession.isPending || draftDirty}
                  onClick={() => commitSession.mutate()}
                  title={draftDirty ? "Save draft edits before committing" : undefined}
                >
                  {commitSession.isPending
                    ? "Creating tickets…"
                    : `Create ${selectedCount} ticket${selectedCount === 1 ? "" : "s"} in ${workspaceSlug}`}
                </button>
              )}
              {commitSession.isError && (
                <p className="modal-hint" style={{ color: "var(--rdl)", marginTop: 8 }}>
                  {(commitSession.error as Error).message}
                </p>
              )}
              {commitSession.isSuccess && (
                <p className="modal-hint" style={{ color: "var(--grn)", marginTop: 8 }}>
                  Created {commitSession.data.created_count} tickets in the workspace.
                </p>
              )}
            </section>
          </div>
        ) : null}
      </main>

      <TicketStudioDraftModal
        item={expandedDraftItem}
        allItems={localDraft}
        agentOptions={studioAgents.data ?? []}
        isOpen={expandedDraftIndex != null}
        readOnly={isReadOnly}
        onClose={() => setExpandedDraftIndex(null)}
        onSave={
          isReadOnly
            ? undefined
            : (updated) => {
                if (expandedDraftIndex == null) return;
                updateDraftItem(expandedDraftIndex, updated);
              }
        }
      />

      <TriageModelModal
        open={modelModalOpen}
        runtime={runtime}
        runtimeOptions={runtimeOptions}
        isSaving={saveRuntime.isPending}
        onClose={() => setModelModalOpen(false)}
        onSave={async (next) => {
          await saveRuntime.mutateAsync(next);
        }}
      />
    </>
  );
}
