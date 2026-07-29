import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import type { Approval, RuntimeOptions, TicketDetail, WorkspaceRuntimeSettings } from "../api/client";
import { api } from "../api/client";

import { formatApprovalResolveError } from "../utils/approvalErrors";
import { TRIAGE_AGENT_NAME } from "../lib/triageAgent";
import { DEFAULT_RUNTIME } from "../lib/runtimeSettings";
import { BaxterAvatar } from "./chat/BaxterAvatar";
import { StudioChatComposer, StudioChatMessages } from "./studio/StudioChat";
import { TreeExpandChevron } from "./icons/TicketTreeIcons";
import { PendingApprovalsSection } from "./PendingApprovalsSection";
import { TriageModelModal } from "./TriageModelModal";
import { runtimeSummaryLabel } from "./WorkspaceRuntimeFields";
import { useApprovalResolution } from "../hooks/useApprovalResolution";
import { useTicketChatSession } from "../hooks/useTicketChatSession";
import { useTriageSession } from "../hooks/useTriageSession";
import "../pages/BaxterChatPage.css";
import "./TriagePanel.css";

const EMPTY_CHIPS = [
  "What is blocking this ticket?",
  "Summarise the last failure",
  "What should we do next?",
] as const;

const AUTO_APPROVE_STORAGE_KEY = "loregarden.triage.autoApprove";

function readStoredAutoApprove(): boolean {
  try {
    return localStorage.getItem(AUTO_APPROVE_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function approvalKindLabel(kind: Approval["kind"]) {
  switch (kind) {
    case "workflow_gate":
      return "Stage sign-off";
    case "cli_permission":
      return "Agent permission";
    case "cli_question":
      return "Agent question";
    default:
      return kind;
  }
}

function ResolvedApprovalSummary({ approval }: { approval: Approval }) {
  const answers = approval.resolved_answers;
  return (
    <div className="triage-resolved-card">
      <div className="triage-resolved-card-title">{approval.title}</div>
      <div className="triage-resolved-card-meta">
        {approvalKindLabel(approval.kind)} · {approval.stage_name} · {approval.status}
      </div>
      {answers ? (
        <pre className="triage-resolved-card-answers">{JSON.stringify(answers, null, 2)}</pre>
      ) : null}
    </div>
  );
}

function greetingFor(now: Date): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** Owns draft locally so typing does not re-render the whole triage panel. */
function TriageHeroAsk({
  onSend,
  busy,
  error,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  error?: string | null;
}) {
  const [draft, setDraft] = useState("");

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  };

  return (
    <section className="baxter-chat-hero" aria-label="Ask Baxter">
      <div className="baxter-chat-hero-avatar">
        <BaxterAvatar variant="head" state="idle" size={64} label={TRIAGE_AGENT_NAME} />
      </div>
      <div className="baxter-chat-hero-body">
        <StudioChatComposer
          value={draft}
          onChange={setDraft}
          onSubmit={submit}
          placeholder="What should we look at on this ticket?"
          sendLabel="Ask Baxter"
          isSending={busy}
          disabled={busy}
          variant="dock"
          iconOnlySend={false}
          error={error}
        />
        <div className="lg-chat-chip-row baxter-chat-chip-row" role="list">
          {EMPTY_CHIPS.map((chip) => (
            <button
              key={chip}
              type="button"
              className="lg-chat-chip baxter-chat-chip"
              role="listitem"
              onClick={() => setDraft(chip)}
            >
              {chip}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

/** Owns draft locally so typing does not re-render the message thread. */
function TriageReplyDock({
  onSend,
  busy,
  optionsRow,
  error,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  optionsRow?: ReactNode;
  error?: string | null;
}) {
  const [draft, setDraft] = useState("");

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  };

  return (
    <div className="baxter-chat-dock">
      <StudioChatComposer
        value={draft}
        onChange={setDraft}
        onSubmit={submit}
        placeholder="Reply to Baxter…"
        sendLabel="Send"
        isSending={busy}
        disabled={busy}
        variant="dock"
        showShortcut
        optionsRow={optionsRow}
        error={error}
      />
    </div>
  );
}

export function TriagePanel({
  ticket,
  runtimeOptions,
  onResolved,
}: {
  ticket: TicketDetail | undefined;
  runtimeOptions: RuntimeOptions | undefined;
  onResolved?: () => void;
}) {
  const qc = useQueryClient();
  const [recentExpanded, setRecentExpanded] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [autoApprove, setAutoApprove] = useState(readStoredAutoApprove);
  const [modelModalOpen, setModelModalOpen] = useState(false);
  const now = useMemo(() => new Date(), []);

  const { triage, pending, isBusy } = useTriageSession(ticket?.id);
  const resolveApproval = useApprovalResolution(ticket?.id, onResolved);
  const session = useTicketChatSession(ticket?.id);

  const messages = triage.data?.messages ?? [];
  const recent = triage.data?.recent_approvals ?? [];
  const thinking = isBusy || session.isBusy;
  const isEmpty = messages.length === 0 && !thinking;
  const savedRuntime = triage.data?.runtime ?? DEFAULT_RUNTIME;
  const modelLabel = runtimeSummaryLabel(savedRuntime, runtimeOptions);

  const saveRuntime = useMutation({
    mutationFn: (runtime: WorkspaceRuntimeSettings) => api.setTriageRuntime(ticket!.id, runtime),
    onSuccess: (saved) => {
      qc.setQueryData(["triage", ticket!.id], (current: typeof triage.data) =>
        current ? { ...current, runtime: saved } : current,
      );
    },
  });

  useEffect(() => {
    setRecentExpanded(false);
    setAutoScroll(true);
  }, [ticket?.id]);

  const setAndStoreAutoApprove = (value: boolean) => {
    setAutoApprove(value);
    try {
      localStorage.setItem(AUTO_APPROVE_STORAGE_KEY, value ? "1" : "0");
    } catch {
      // localStorage unavailable
    }
  };

  const sendChat = (text: string) => {
    if (!text || thinking || !ticket) return;
    void session
      .send(text, { autoApprove })
      .then(() => onResolved?.())
      .catch(() => {});
  };

  if (!ticket) {
    return <div className="triage-panel-empty">Select a ticket to triage</div>;
  }

  const contextPill = (
    <span className="baxter-chat-context" title={ticket.title}>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <path d="M14 2v6h6" />
      </svg>
      {ticket.external_id}
      {ticket.branch ? ` · ${ticket.branch}` : ""}
    </span>
  );

  const composerError =
    session.error ??
    (saveRuntime.isError
      ? (saveRuntime.error as Error)?.message || "Failed to save triage model settings"
      : null);

  const optionsRow = (
    <div className="studio-chat-composer-options-inline">
      <label
        className="chat-composer-option"
        title="Auto-approve Baxter's tool permissions for this turn. Questions and out-of-scope actions still ask."
      >
        <input
          type="checkbox"
          checked={autoApprove}
          onChange={(e) => setAndStoreAutoApprove(e.target.checked)}
        />
        Auto-approve
      </label>
      <label className="chat-composer-option">
        <input
          type="checkbox"
          checked={autoScroll}
          onChange={(e) => setAutoScroll(e.target.checked)}
        />
        Auto-scroll
      </label>
    </div>
  );

  return (
    <div className={`baxter-chat triage-baxter-chat lg-chat-surface${isEmpty ? " baxter-chat--empty" : ""}`}>
      <header className="baxter-chat-top">
        <div className="baxter-chat-brand">
          <BaxterAvatar variant="head" state={thinking ? "typing" : "idle"} size={32} label={TRIAGE_AGENT_NAME} />
          <span className="baxter-chat-name">{TRIAGE_AGENT_NAME}</span>
          {contextPill}
        </div>
        <button
          type="button"
          className="baxter-chat-new"
          disabled={!runtimeOptions || triage.isLoading}
          onClick={() => setModelModalOpen(true)}
          title="Baxter model settings"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <circle cx="12" cy="12" r="10" />
            <path d="M12 6v6l4 2" />
          </svg>
          Model · {modelLabel}
        </button>
      </header>

      {triage.isError ? (
        <div className="triage-panel-alert triage-panel-alert--bar">
          Triage API unavailable — showing inbox approvals for this ticket.
        </div>
      ) : null}

      {isEmpty ? (
        <div className="baxter-chat-welcome">
          {recent.length > 0 ? (
            <section className="triage-panel-section">
              <button
                type="button"
                onClick={() => setRecentExpanded((open) => !open)}
                aria-expanded={recentExpanded}
                className="triage-panel-section-toggle"
              >
                <TreeExpandChevron expanded={recentExpanded} />
                <span className="state-label" style={{ marginBottom: 0 }}>
                  Recently resolved
                </span>
                <span className="triage-panel-section-count">{recent.length}</span>
              </button>
              {recentExpanded ? (
                <div className="triage-panel-section-scroll">
                  {recent.map((approval) => (
                    <ResolvedApprovalSummary key={approval.id} approval={approval} />
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}

          <header className="baxter-chat-intro">
            <p className="baxter-chat-kicker">{ticket.external_id}</p>
            <h1 className="baxter-chat-greeting">{greetingFor(now)}</h1>
            <p className="baxter-chat-summary">
              Ask Baxter about {ticket.title || "this ticket"} — workflow state, failures, and next steps.
            </p>
          </header>

          <TriageHeroAsk
            key={ticket.id}
            onSend={sendChat}
            busy={thinking}
            error={composerError}
          />
        </div>
      ) : (
        <>
          <div className="baxter-chat-thread" aria-live="polite">
            {recent.length > 0 ? (
              <section className="triage-panel-section">
                <button
                  type="button"
                  onClick={() => setRecentExpanded((open) => !open)}
                  aria-expanded={recentExpanded}
                  className="triage-panel-section-toggle"
                >
                  <TreeExpandChevron expanded={recentExpanded} />
                  <span className="state-label" style={{ marginBottom: 0 }}>
                    Recently resolved
                  </span>
                  <span className="triage-panel-section-count">{recent.length}</span>
                </button>
                {recentExpanded ? (
                  <div className="triage-panel-section-scroll">
                    {recent.map((approval) => (
                      <ResolvedApprovalSummary key={approval.id} approval={approval} />
                    ))}
                  </div>
                ) : null}
              </section>
            ) : null}

            <StudioChatMessages
              messages={messages}
              assistantLabel={TRIAGE_AGENT_NAME}
              thinkingActivity="typing"
              autoScroll={autoScroll}
              isThinking={thinking}
              thinkingMessage="Baxter is looking…"
              thinkingSub="Reading this ticket’s context and recent runs"
              showAssistantAvatar={false}
            />
          </div>

          <PendingApprovalsSection
            approvals={pending}
            ticketExternalId={ticket.external_id}
            submittingApprovalId={
              resolveApproval.isPending ? resolveApproval.variables?.id ?? null : null
            }
            submitError={
              resolveApproval.isError ? formatApprovalResolveError(resolveApproval.error) : null
            }
            onApprove={(approval, payload) =>
              resolveApproval.mutate({ id: approval.id, action: "approve", ...payload })
            }
            onReject={(approval, payload) =>
              resolveApproval.mutate({ id: approval.id, action: "reject", ...payload })
            }
          />

          <TriageReplyDock
            key={ticket.id}
            onSend={sendChat}
            busy={thinking}
            optionsRow={optionsRow}
            error={composerError}
          />
        </>
      )}

      {pending.length > 0 && isEmpty ? (
        <PendingApprovalsSection
          approvals={pending}
          ticketExternalId={ticket.external_id}
          submittingApprovalId={
            resolveApproval.isPending ? resolveApproval.variables?.id ?? null : null
          }
          submitError={
            resolveApproval.isError ? formatApprovalResolveError(resolveApproval.error) : null
          }
          onApprove={(approval, payload) =>
            resolveApproval.mutate({ id: approval.id, action: "approve", ...payload })
          }
          onReject={(approval, payload) =>
            resolveApproval.mutate({ id: approval.id, action: "reject", ...payload })
          }
        />
      ) : null}

      <TriageModelModal
        open={modelModalOpen}
        runtime={savedRuntime}
        runtimeOptions={runtimeOptions}
        isSaving={saveRuntime.isPending}
        onClose={() => setModelModalOpen(false)}
        onSave={async (runtime) => {
          await saveRuntime.mutateAsync(runtime);
        }}
      />
    </div>
  );
}
