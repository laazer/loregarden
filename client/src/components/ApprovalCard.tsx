import { useEffect, useMemo, useState } from "react";

import type { AgentQuestion, Approval } from "../api/client";
import { PermissionDetails } from "./PermissionDetails";
import { RejectApprovalModal } from "./RejectApprovalModal";

export type ApprovalResolvePayload = {
  answers?: Record<string, string | string[]>;
  response?: string;
  always_allow?: boolean;
  allow_for_ticket?: boolean;
  allow_for_stage?: boolean;
  route_to_stage_key?: string;
};

function questionList(approval: Approval): AgentQuestion[] {
  if (approval.questions?.length) return approval.questions;
  try {
    const payload = JSON.parse(approval.tool_input_json || "{}") as { questions?: AgentQuestion[] };
    return payload.questions ?? [];
  } catch {
    return [];
  }
}

function answersComplete(
  questions: AgentQuestion[],
  answers: Record<string, string | string[]>,
  freeformResponse: string,
): boolean {
  if (freeformResponse.trim()) return true;
  return questions.every((q) => {
    const answer = answers[q.question];
    if (Array.isArray(answer)) return answer.some((part) => part.trim());
    return Boolean(answer?.trim());
  });
}

export function ApprovalCard({
  approval,
  onApprove,
  onReject,
  onInspect,
  isSubmitting,
  compact = false,
}: {
  approval: Approval;
  onApprove: (payload?: ApprovalResolvePayload) => void;
  onReject: (payload?: ApprovalResolvePayload) => void;
  onInspect?: () => void;
  isSubmitting?: boolean;
  compact?: boolean;
}) {
  const isQuestion = approval.kind === "cli_question";
  const isPermission = approval.kind === "cli_permission";
  const isGate = approval.kind === "workflow_gate";
  const questions = useMemo(() => (isQuestion ? questionList(approval) : []), [approval, isQuestion]);
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  const [customText, setCustomText] = useState<Record<string, string>>({});
  const [freeformResponse, setFreeformResponse] = useState("");
  const [alwaysAllowWorkspace, setAlwaysAllowWorkspace] = useState(false);
  const [allowForTicket, setAllowForTicket] = useState(false);
  const [allowForStage, setAllowForStage] = useState(false);
  const [checkedItems, setCheckedItems] = useState<Record<number, boolean>>({});
  const [reworkEnabled, setReworkEnabled] = useState(false);
  const [reworkStageKey, setReworkStageKey] = useState("");
  const [reworkNote, setReworkNote] = useState("");
  const [rejectModalOpen, setRejectModalOpen] = useState(false);

  const routeOptions = isGate ? approval.route_options ?? [] : [];

  useEffect(() => {
    setAnswers({});
    setCustomText({});
    setFreeformResponse("");
    setAlwaysAllowWorkspace(false);
    setAllowForTicket(false);
    setAllowForStage(false);
    setCheckedItems({});
    setReworkEnabled(false);
    setReworkStageKey("");
    setReworkNote("");
    setRejectModalOpen(false);
  }, [approval.id]);

  const reworkActive = reworkEnabled && !!reworkStageKey;
  const canSubmit =
    (!isQuestion || answersComplete(questions, answers, freeformResponse)) &&
    (!reworkEnabled || !!reworkStageKey);

  const resolvePayload = (): ApprovalResolvePayload => ({
    always_allow: alwaysAllowWorkspace || undefined,
    allow_for_ticket: allowForTicket || undefined,
    allow_for_stage: allowForStage || undefined,
    ...(reworkActive
      ? { route_to_stage_key: reworkStageKey, response: reworkNote.trim() || undefined }
      : {}),
  });

  const submitAnswers = () => {
    const merged: Record<string, string | string[]> = { ...answers };
    for (const [question, text] of Object.entries(customText)) {
      if (text.trim()) merged[question] = text.trim();
    }
    onApprove({
      answers: Object.keys(merged).length ? merged : undefined,
      response: freeformResponse.trim() || undefined,
      ...resolvePayload(),
    });
  };

  const submitApproval = () => {
    onApprove(resolvePayload());
  };

  return (
    <div
      style={{
        border: "1px solid var(--bd)",
        borderRadius: 12,
        background: "var(--bg2)",
        marginBottom: 10,
        overflow: "hidden",
      }}
    >
      <div style={{ padding: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>{approval.title}</div>
        <div style={{ fontSize: 11, color: "var(--txl)", marginBottom: 8 }}>
          {approval.stage_name}
          {approval.kind === "workflow_gate" && <span> · stage sign-off</span>}
          {approval.kind === "cli_permission" && approval.cli_adapter && (
            <span> · {approval.cli_adapter} permission</span>
          )}
          {isQuestion && approval.cli_adapter && <span> · {approval.cli_adapter} question</span>}
          {!compact && approval.workspace_slug && <span> · {approval.workspace_slug}</span>}
        </div>
        <p
          style={{ margin: 0, fontSize: 12, color: "var(--txm)", lineHeight: 1.55, whiteSpace: "pre-line" }}
        >
          {approval.impact}
        </p>

        {!!approval.checklist?.length && (
          <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: "var(--ac2)" }}>
              Testing checklist (notes only, not required to approve)
            </div>
            {approval.checklist.map((item, idx) => (
              <label
                key={idx}
                style={{
                  display: "flex",
                  gap: 8,
                  alignItems: "flex-start",
                  fontSize: 12,
                  color: checkedItems[idx] ? "var(--txl)" : "var(--txm)",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={!!checkedItems[idx]}
                  onChange={() =>
                    setCheckedItems((prev) => ({ ...prev, [idx]: !prev[idx] }))
                  }
                  style={{ marginTop: 2 }}
                />
                <span style={{ textDecoration: checkedItems[idx] ? "line-through" : "none" }}>{item}</span>
              </label>
            ))}
          </div>
        )}

        {isGate && routeOptions.length > 0 && (
          <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            <label
              style={{
                display: "flex",
                gap: 8,
                alignItems: "flex-start",
                fontSize: 12,
                color: "var(--txm)",
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={reworkEnabled}
                disabled={isSubmitting}
                onChange={(e) => setReworkEnabled(e.target.checked)}
                style={{ marginTop: 2 }}
              />
              <span>
                <span style={{ fontWeight: 600 }}>Approve, then send back through the workflow</span>
                <div style={{ fontSize: 11, color: "var(--txl)", marginTop: 2 }}>
                  Sign off on the verification, but route the ticket to an earlier stage so
                  prototype fixes get rebuilt with proper code and tests.
                </div>
              </span>
            </label>
            {reworkEnabled && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingLeft: 22 }}>
                <select
                  value={reworkStageKey}
                  disabled={isSubmitting}
                  onChange={(e) => setReworkStageKey(e.target.value)}
                  style={{
                    padding: "8px 10px",
                    borderRadius: 8,
                    border: "1px solid var(--bd)",
                    background: "var(--bg2)",
                    color: "var(--tx)",
                    fontSize: 12,
                  }}
                >
                  <option value="">Route back to stage…</option>
                  {routeOptions.map((option) => (
                    <option key={option.key} value={option.key}>
                      {option.name}
                    </option>
                  ))}
                </select>
                <textarea
                  value={reworkNote}
                  disabled={isSubmitting}
                  onChange={(e) => setReworkNote(e.target.value)}
                  placeholder="What should be formalized? (e.g. prototype fixes made during the playtest)"
                  rows={2}
                  style={{
                    width: "100%",
                    padding: "8px 10px",
                    borderRadius: 8,
                    border: "1px solid var(--bd)",
                    background: "var(--bg2)",
                    color: "var(--tx)",
                    fontSize: 12,
                    resize: "vertical",
                  }}
                />
              </div>
            )}
          </div>
        )}

        {isQuestion && questions.length > 0 && (
          <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 14 }}>
            {questions.map((q) => (
              <div
                key={q.question}
                style={{
                  padding: 12,
                  borderRadius: 10,
                  border: "1px solid var(--bd)",
                  background: "var(--bg3)",
                }}
              >
                {q.header && (
                  <div style={{ fontSize: 10, fontWeight: 700, color: "var(--ac2)", marginBottom: 6 }}>
                    {q.header}
                  </div>
                )}
                <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 10 }}>{q.question}</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {q.options.map((option) => {
                    const selected = q.multiSelect
                      ? ((answers[q.question] as string[] | undefined) ?? []).includes(option.label)
                      : answers[q.question] === option.label;
                    return (
                      <label
                        key={option.label}
                        style={{
                          display: "flex",
                          gap: 10,
                          alignItems: "flex-start",
                          padding: "8px 10px",
                          borderRadius: 8,
                          border: `1px solid ${selected ? "rgba(96,165,250,.45)" : "var(--bd)"}`,
                          background: selected ? "rgba(96,165,250,.08)" : "var(--bg2)",
                          cursor: "pointer",
                        }}
                      >
                        <input
                          type={q.multiSelect ? "checkbox" : "radio"}
                          name={q.question}
                          checked={selected}
                          onChange={() => {
                            if (q.multiSelect) {
                              const current = ((answers[q.question] as string[] | undefined) ?? []).slice();
                              const idx = current.indexOf(option.label);
                              if (idx >= 0) current.splice(idx, 1);
                              else current.push(option.label);
                              setAnswers((prev) => ({ ...prev, [q.question]: current }));
                            } else {
                              setAnswers((prev) => ({ ...prev, [q.question]: option.label }));
                              setCustomText((prev) => ({ ...prev, [q.question]: "" }));
                            }
                          }}
                          style={{ marginTop: 3 }}
                        />
                        <span>
                          <div style={{ fontSize: 12, fontWeight: 600 }}>{option.label}</div>
                          {option.description && (
                            <div style={{ fontSize: 11, color: "var(--txm)", marginTop: 3 }}>{option.description}</div>
                          )}
                        </span>
                      </label>
                    );
                  })}
                </div>
                <input
                  type="text"
                  placeholder="Or type a custom answer…"
                  value={customText[q.question] ?? ""}
                  onChange={(e) => {
                    const value = e.target.value;
                    setCustomText((prev) => ({ ...prev, [q.question]: value }));
                    if (value.trim()) {
                      setAnswers((prev) => ({ ...prev, [q.question]: value }));
                    }
                  }}
                  style={{
                    marginTop: 10,
                    width: "100%",
                    padding: "8px 10px",
                    borderRadius: 8,
                    border: "1px solid var(--bd)",
                    background: "var(--bg2)",
                    color: "var(--tx)",
                    fontSize: 12,
                  }}
                />
              </div>
            ))}
            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 11, color: "var(--txm)" }}>
              Optional freeform reply (instead of structured answers)
              <textarea
                value={freeformResponse}
                onChange={(e) => setFreeformResponse(e.target.value)}
                rows={3}
                style={{
                  width: "100%",
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: "1px solid var(--bd)",
                  background: "var(--bg2)",
                  color: "var(--tx)",
                  fontSize: 12,
                  resize: "vertical",
                }}
              />
            </label>
          </div>
        )}

        {isPermission && approval.tool_name && (
          <>
            <PermissionDetails toolName={approval.tool_name} toolInputJson={approval.tool_input_json} />
            <div className="permission-allow-scopes">
              <div className="permission-allow-scopes-title">Remember this approval</div>
              {approval.workspace_slug && (
                <label className="permission-always-allow">
                  <input
                    type="checkbox"
                    checked={alwaysAllowWorkspace}
                    disabled={isSubmitting}
                    onChange={(e) => setAlwaysAllowWorkspace(e.target.checked)}
                  />
                  <span>
                    Always allow in workspace <strong>{approval.workspace_slug}</strong>
                  </span>
                </label>
              )}
              {(approval.ticket_external_id || approval.ticket_id) && (
                <label className="permission-always-allow">
                  <input
                    type="checkbox"
                    checked={allowForTicket}
                    disabled={isSubmitting}
                    onChange={(e) => setAllowForTicket(e.target.checked)}
                  />
                  <span>
                    Always allow for ticket{" "}
                    <strong>{approval.ticket_external_id || approval.ticket_id}</strong>
                  </span>
                </label>
              )}
              {approval.stage_name && (
                <label className="permission-always-allow">
                  <input
                    type="checkbox"
                    checked={allowForStage}
                    disabled={isSubmitting}
                    onChange={(e) => setAllowForStage(e.target.checked)}
                  />
                  <span>
                    Always allow for stage <strong>{approval.stage_name}</strong>
                  </span>
                </label>
              )}
            </div>
          </>
        )}
      </div>
      <div style={{ display: "flex", borderTop: "1px solid var(--bd)" }}>
        {isQuestion ? (
          <button
            type="button"
            className="btn-secondary"
            style={{ flex: 1, borderRadius: 0, color: "var(--grl)" }}
            disabled={!canSubmit || isSubmitting}
            onClick={submitAnswers}
          >
            Submit answers
          </button>
        ) : (
          <button
            type="button"
            className="btn-secondary"
            style={{ flex: 1, borderRadius: 0, color: "var(--grl)" }}
            disabled={!canSubmit || isSubmitting}
            onClick={submitApproval}
          >
            {isPermission ? "Allow" : reworkActive ? "Approve & route back" : "Approve"}
          </button>
        )}
        <button
          type="button"
          className="btn-secondary"
          style={{ flex: 1, borderRadius: 0, color: "var(--rdl)" }}
          disabled={isSubmitting}
          onClick={isGate ? () => setRejectModalOpen(true) : () => onReject()}
        >
          {isQuestion ? "Decline" : isPermission ? "Deny" : "Reject"}
        </button>
        {!compact && onInspect && (
          <button type="button" className="btn-secondary" style={{ flex: 1, borderRadius: 0 }} onClick={onInspect}>
            Inspect
          </button>
        )}
      </div>
      {isGate && (
        <RejectApprovalModal
          open={rejectModalOpen}
          approval={approval}
          isSubmitting={isSubmitting}
          onClose={() => setRejectModalOpen(false)}
          onConfirm={(payload) => {
            setRejectModalOpen(false);
            onReject(payload);
          }}
        />
      )}
    </div>
  );
}
