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

const DEFAULT_RUNTIME: WorkspaceRuntimeSettings = {
  cli_adapter: "default",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};

const TYPE_OPTIONS = ["feature", "capability", "task", "bug", "milestone"] as const;

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

  const sessions = useQuery({
    queryKey: ["ticket-studio-sessions", workspaceSlug],
    queryFn: () => api.ticketStudioSessions(workspaceSlug),
    enabled: !!workspaceSlug,
  });

  const selectedSession = useMemo(
    () => sessions.data?.find((s) => s.id === selectedSessionId) ?? null,
    [sessions.data, selectedSessionId],
  );

  useEffect(() => {
    if (!selectedSession) {
      setLocalDraft([]);
      setDraftDirty(false);
      return;
    }
    setLocalDraft(selectedSession.draft);
    setDraftDirty(false);
  }, [selectedSession?.id, selectedSession?.draft, selectedSession?.updated_at]);

  const createSession = useMutation({
    mutationFn: () =>
      api.createTicketStudioSession({
        workspace_slug: workspaceSlug,
        title: newDraft.title.trim(),
        brief: newDraft.brief.trim(),
        parent_ticket_id: newDraft.parent_ticket_id || null,
      }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["ticket-studio-sessions", workspaceSlug] });
      setSelectedSessionId(created.id);
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

  const updateDraftItem = (index: number, patch: Partial<TicketStudioDraftItem>) => {
    setLocalDraft((items) => items.map((item, idx) => (idx === index ? { ...item, ...patch } : item)));
    setDraftDirty(true);
  };

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
              {createSession.isPending ? "Creating…" : "Start scoping session"}
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
                    {isReadOnly ? "Committed to workspace" : "Chat with Planner to refine scope"}
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

              {selectedSession.clarifying_questions.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div className="modal-section-title">Clarifying questions</div>
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12.5, color: "var(--txm)" }}>
                    {selectedSession.clarifying_questions.map((q) => (
                      <li key={q}>{q}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div
                className="state-card"
                style={{
                  marginTop: 14,
                  maxHeight: 280,
                  overflow: "auto",
                  padding: 12,
                  fontSize: 12.5,
                }}
              >
                {selectedSession.messages.length === 0 ? (
                  <p className="modal-hint" style={{ margin: 0 }}>
                    No messages yet. Generate scope or ask the agent to refine the breakdown.
                  </p>
                ) : (
                  selectedSession.messages.map((msg) => (
                    <div key={msg.id} style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: 10.5, color: "var(--txl)", marginBottom: 2 }}>
                        {msg.role === "user" ? "You" : "Planner"}
                      </div>
                      <div style={{ whiteSpace: "pre-wrap" }}>{msg.content}</div>
                    </div>
                  ))
                )}
              </div>

              {!isReadOnly && (
                <>
                  <textarea
                    value={chatDraft}
                    onChange={(e) => setChatDraft(e.target.value)}
                    rows={3}
                    placeholder="Ask to add tasks, split capabilities, tighten acceptance criteria…"
                    style={{
                      width: "100%",
                      marginTop: 10,
                      padding: "10px 12px",
                      borderRadius: 10,
                      border: "1px solid var(--bd)",
                      background: "var(--bg2)",
                      color: "var(--tx)",
                      fontSize: 12.5,
                      resize: "vertical",
                      boxSizing: "border-box",
                    }}
                  />
                  <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
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
                      disabled={generateScope.isPending || sendMessage.isPending}
                      onClick={() => generateScope.mutate()}
                    >
                      {generateScope.isPending ? "Scoping…" : "Generate scope"}
                    </button>
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={!chatDraft.trim() || sendMessage.isPending}
                      onClick={() => sendMessage.mutate(chatDraft.trim())}
                    >
                      {sendMessage.isPending ? "Sending…" : "Send"}
                    </button>
                  </div>
                </>
              )}
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
                <p className="modal-hint">Generate scope to populate draft tickets.</p>
              ) : (
                localDraft.map((item, index) => (
                  <div key={item.ref} className="state-card" style={{ marginBottom: 10, padding: 10 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
                      <input
                        type="checkbox"
                        checked={item.selected}
                        disabled={isReadOnly}
                        onChange={(e) => updateDraftItem(index, { selected: e.target.checked })}
                      />
                      <select
                        className="btn-secondary filter-select"
                        style={{ width: 110 }}
                        value={item.work_item_type}
                        disabled={isReadOnly}
                        onChange={(e) =>
                          updateDraftItem(index, {
                            work_item_type: e.target.value as TicketStudioDraftItem["work_item_type"],
                          })
                        }
                      >
                        {TYPE_OPTIONS.map((t) => (
                          <option key={t} value={t}>
                            {workItemTypeLabel(t)}
                          </option>
                        ))}
                      </select>
                      <input
                        className="btn-secondary"
                        style={{ flex: 1, boxSizing: "border-box" }}
                        value={item.title}
                        readOnly={isReadOnly}
                        onChange={(e) => updateDraftItem(index, { title: e.target.value })}
                      />
                    </div>
                    {item.parent_ref && (
                      <div style={{ fontSize: 10.5, color: "var(--txl)", marginBottom: 6, fontFamily: "var(--mono)" }}>
                        parent: {item.parent_ref}
                      </div>
                    )}
                    {!isReadOnly ? (
                      <textarea
                        className="btn-secondary"
                        style={{ width: "100%", minHeight: 48, boxSizing: "border-box", fontSize: 11.5 }}
                        value={item.description}
                        onChange={(e) => updateDraftItem(index, { description: e.target.value })}
                      />
                    ) : (
                      <p style={{ margin: 0, fontSize: 11.5, color: "var(--txm)" }}>{item.description}</p>
                    )}
                    {item.acceptance_criteria.length > 0 && (
                      <ul style={{ margin: "6px 0 0", paddingLeft: 16, fontSize: 11, color: "var(--txl)" }}>
                        {item.acceptance_criteria.map((ac) => (
                          <li key={ac}>{ac}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))
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
