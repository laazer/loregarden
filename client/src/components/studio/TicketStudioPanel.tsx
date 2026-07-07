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
import { TicketStudioChatMessages, TicketStudioComposer } from "./TicketStudioChat";
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

  const selectedWorkspace = workspaces.find((ws) => ws.slug === workspaceSlug);
  const workspaceInitial = (selectedWorkspace?.name ?? workspaceSlug).charAt(0).toUpperCase();

  const renderDraftPanel = () => (
    <aside className="ticket-studio-drafts">
      <div className="ticket-studio-drafts-header">
        <span className="ticket-studio-drafts-title">Draft tickets</span>
        {selectedSession && (
          <span className="ticket-studio-selected-badge">{selectedCount} selected</span>
        )}
        {selectedSession && !isReadOnly && draftDirty && (
          <button
            type="button"
            className="btn-secondary btn-compact"
            style={{ marginLeft: "auto" }}
            disabled={saveDraft.isPending}
            onClick={() => saveDraft.mutate()}
          >
            Save edits
          </button>
        )}
      </div>

      {!selectedSession ? (
        <p className="studio-preview-hint">Start a scoping session to generate draft tickets.</p>
      ) : localDraft.length === 0 ? (
        <p className="studio-preview-hint">
          {hasOpenQuestions && !clarifyingResolved
            ? "Answer clarifying questions, then generate tickets."
            : "Review the brief or generate tickets to populate the draft."}
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {localDraft.map((item, index) => {
            const summary = draftSummaryLine(item);
            return (
              <div
                key={item.ref}
                className={`ticket-draft-card${item.selected ? " selected" : ""}`}
              >
                <div style={{ display: "flex", alignItems: "flex-start", gap: 11 }}>
                  <button
                    type="button"
                    className={`ticket-draft-check${item.selected ? " checked" : ""}`}
                    disabled={isReadOnly}
                    onClick={() => updateDraftItem(index, { selected: !item.selected })}
                    aria-label={`Include ${item.title}`}
                  >
                    {item.selected && (
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--onac)" strokeWidth="3.2">
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                    )}
                  </button>
                  <button
                    type="button"
                    style={{
                      flex: 1,
                      minWidth: 0,
                      border: "none",
                      background: "transparent",
                      padding: 0,
                      textAlign: "left",
                      cursor: "pointer",
                      color: "inherit",
                      font: "inherit",
                    }}
                    onClick={() => setExpandedDraftIndex(index)}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                      <span className={`ticket-draft-type ${item.work_item_type}`}>
                        {workItemTypeLabel(item.work_item_type)}
                      </span>
                      {summary && <span className="ticket-draft-meta">{summary}</span>}
                    </div>
                    <div className="ticket-draft-title">{item.title}</div>
                    {item.description && <div className="ticket-draft-desc">{item.description}</div>}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {selectedSession && !isReadOnly && localDraft.length > 0 && (
        <button
          type="button"
          className="studio-library-cta ticket-studio-commit-btn"
          disabled={selectedCount === 0 || commitSession.isPending || draftDirty}
          onClick={() => commitSession.mutate()}
          title={draftDirty ? "Save draft edits before committing" : undefined}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
          {commitSession.isPending
            ? "Creating tickets…"
            : `Create ${selectedCount} ticket${selectedCount === 1 ? "" : "s"} under milestone`}
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
    </aside>
  );

  return (
    <>
      <div className="ticket-studio-shell">
        <aside className="ticket-studio-rail">
          <div className="studio-library-section-label">Workspace</div>
          <div className="ticket-studio-workspace-card">
            <span className="ticket-studio-workspace-mark">{workspaceInitial}</span>
            <select
              className="ticket-studio-workspace-select"
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
            <svg
              className="ticket-studio-workspace-chevron"
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--txl)"
              strokeWidth="2"
              aria-hidden
            >
              <path d="m6 9 6 6 6-6" />
            </svg>
          </div>

          <button
            type="button"
            className="studio-library-cta"
            style={{ marginBottom: 16 }}
            onClick={() => {
              setSelectedSessionId(null);
              setNewDraft(emptySessionDraft());
            }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
              <path d="M12 5v14M5 12h14" />
            </svg>
            New scope
          </button>

          <div className="studio-library-section-label">Sessions</div>
          <div className="ticket-studio-session-list">
            {(sessions.data ?? []).map((session) => (
              <button
                key={session.id}
                type="button"
                className={`ticket-studio-session-item${selectedSessionId === session.id ? " active" : ""}`}
                onClick={() => setSelectedSessionId(session.id)}
              >
                <div className="ticket-studio-session-title">{session.title}</div>
                <div className="ticket-studio-session-meta">
                  {session.status !== "committed" && (
                    <span className="ticket-studio-status-draft">{session.status}</span>
                  )}
                  <span>
                    {session.draft.length} draft ticket{session.draft.length === 1 ? "" : "s"}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <div className="ticket-studio-chat">
          {!selectedSessionId ? (
            <>
              <div className="ticket-studio-chat-header">
                <h2 className="ticket-studio-chat-title">New feature scope</h2>
                <p className="ticket-studio-chat-sub">
                  Describe a feature and let the Planner agent decompose it into milestones, capabilities, and tasks.
                </p>
              </div>
              <div className="ticket-studio-chat-body ticket-studio-chat-body--new">
                <div className="ticket-studio-new-scope">
                  <div className="studio-field">
                    <div className="studio-field-label">Feature title</div>
                    <input
                      className="studio-input"
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

                  <div className="studio-field">
                    <div className="studio-field-label">Feature brief</div>
                    <textarea
                      className="studio-textarea"
                      value={newDraft.brief}
                      onChange={(e) => setNewDraft({ ...newDraft, brief: e.target.value })}
                      placeholder="Problem, users, constraints, success metrics, technical notes…"
                    />
                  </div>

                  <button
                    type="button"
                    className="studio-library-cta"
                    style={{ width: "fit-content" }}
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
              </div>
            </>
          ) : selectedSession ? (
            <>
              <div className="ticket-studio-chat-header">
                <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <h2 className="ticket-studio-chat-title">{selectedSession.title}</h2>
                    <p className="ticket-studio-chat-sub">
                      {isReadOnly ? "Committed to workspace" : "Chat with the scoper to refine scope"}
                      {selectedSession.parent_ticket_title ? (
                        <>
                          {" "}
                          · under <span style={{ color: "var(--tx)" }}>{selectedSession.parent_ticket_title}</span>
                        </>
                      ) : null}
                    </p>
                  </div>
                  {!isReadOnly && (
                    <button
                      type="button"
                      className="ticket-studio-delete-btn"
                      disabled={deleteSession.isPending}
                      onClick={() => deleteSession.mutate(selectedSession.id)}
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                      </svg>
                      Delete
                    </button>
                  )}
                </div>
              </div>

              <div className="ticket-studio-chat-body">
                {selectedSession.summary && (
                  <div className="ticket-studio-msg-row">
                    <span className="ticket-studio-scoper-avatar" aria-hidden>
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#04140f" strokeWidth="2.4">
                        <path d="M12 3v18M5 8l7-5 7 5" />
                      </svg>
                    </span>
                    <div className="ticket-studio-msg ticket-studio-msg-assistant">
                      <div className="ticket-studio-msg-body">{selectedSession.summary}</div>
                    </div>
                  </div>
                )}

                {hasOpenQuestions && !isReadOnly && (
                  <div className="ticket-studio-clarify-card">
                    <div className="studio-card-title" style={{ marginBottom: 8 }}>
                      Clarifying questions
                    </div>
                    <p className="studio-card-hint" style={{ marginTop: 0 }}>
                      Answer these before generating tickets.
                    </p>
                    {selectedSession.clarifying_questions.map((question, index) => (
                      <div key={question} className="studio-field" style={{ marginBottom: 10 }}>
                        <div className="studio-field-label">{question}</div>
                        <textarea
                          className="studio-textarea"
                          style={{ minHeight: 56 }}
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
                  <div className="ticket-studio-clarify-card">
                    <div className="studio-card-title">Clarifying questions</div>
                    <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12.5, color: "var(--txm)" }}>
                      {selectedSession.clarifying_questions.map((q, index) => (
                        <li key={q}>
                          <div>{q}</div>
                          {selectedSession.clarifying_answers[index] && (
                            <div style={{ color: "var(--txl)", marginTop: 2 }}>
                              {selectedSession.clarifying_answers[index]}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <TicketStudioChatMessages
                  messages={selectedSession.messages}
                  emptyMessage="Review the brief for questions, then generate tickets."
                  isThinking={isScoperThinking}
                  thinkingMessage="Scoper is thinking…"
                />
              </div>

              {!isReadOnly && (
                <TicketStudioComposer
                    value={chatDraft}
                    onChange={setChatDraft}
                    onSubmit={() => sendMessage.mutate(chatDraft.trim())}
                    placeholder="Ask to refine scope, split work, or tighten acceptance criteria…"
                    isSending={sendMessage.isPending}
                    disabled={isScoperThinking}
                    modelLabel={`Model · ${modelLabel}`}
                    modelDisabled={!runtimeOptions}
                    onModelClick={() => setModelModalOpen(true)}
                    onReviewBrief={() => requestClarifications.mutate()}
                    onGenerateTickets={() => generateScope.mutate()}
                    reviewPending={requestClarifications.isPending}
                    generatePending={generateScope.isPending}
                    generateDisabled={!clarifyingResolved || isScoperThinking}
                    generateTitle={!clarifyingResolved ? "Answer clarifying questions first" : undefined}
                />
              )}
            </>
          ) : null}
        </div>

        {renderDraftPanel()}
      </div>

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
