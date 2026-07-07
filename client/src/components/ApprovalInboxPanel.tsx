import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useUiStore } from "../state/uiStore";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { IconCloseButton } from "./IconCloseButton";
import { ApprovalCard } from "./ApprovalCard";

export function ApprovalInboxPanel() {
  const qc = useQueryClient();
  const inboxOpen = useUiStore((s) => s.inboxOpen);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const setSelectedTicketId = useUiStore((s) => s.setSelectedTicketId);
  const setTab = useUiStore((s) => s.setTab);
  const setAppPage = useUiStore((s) => s.setAppPage);

  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
    enabled: inboxOpen,
  });

  const resolveApproval = useMutation({
    mutationFn: ({
      id,
      action,
      answers,
      response,
      always_allow,
      allow_for_ticket,
      allow_for_stage,
    }: {
      id: string;
      action: "approve" | "reject";
      answers?: Record<string, string | string[]>;
      response?: string;
      always_allow?: boolean;
      allow_for_ticket?: boolean;
      allow_for_stage?: boolean;
    }) =>
      api.resolveApproval(id, {
        action,
        answers,
        response,
        always_allow,
        allow_for_ticket,
        allow_for_stage,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  if (!inboxOpen) return null;

  return (
    <>
      <div className="inbox-overlay" onClick={() => setInboxOpen(false)} />
      <aside className="inbox-panel">
        <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--bd)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="pane-title">Global Approval Inbox</span>
            <span className="count-pill">{approvals.data?.length ?? 0}</span>
            <div style={{ flex: 1 }} />
            <IconCloseButton onClick={() => setInboxOpen(false)} />
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
          {resolveApproval.isError && (
            <div
              style={{
                fontSize: 11.5,
                color: "var(--rdl)",
                marginBottom: 12,
                padding: "8px 10px",
                borderRadius: 8,
                background: "rgba(240,96,63,.08)",
                border: "1px solid rgba(240,96,63,.25)",
              }}
            >
              {formatApprovalResolveError(resolveApproval.error)}
            </div>
          )}
          {approvals.data?.map((a) => (
            <ApprovalCard
              key={a.id}
              approval={a}
              onApprove={(payload) =>
                resolveApproval.mutate({ id: a.id, action: "approve", ...payload })
              }
              onReject={() => resolveApproval.mutate({ id: a.id, action: "reject" })}
              onInspect={() => {
                setSelectedTicketId(a.ticket_id);
                setAppPage("dashboard");
                setInboxOpen(false);
                setTab("diff");
              }}
              isSubmitting={resolveApproval.isPending && resolveApproval.variables?.id === a.id}
            />
          ))}
          {!approvals.data?.length && (
            <div style={{ textAlign: "center", color: "var(--txm)", padding: 40 }}>
              Inbox zero — nothing needs your attention
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
