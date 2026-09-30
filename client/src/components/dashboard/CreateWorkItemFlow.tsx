import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type TicketDetail, type TicketSummary, type TicketTreeNode, type WorkItemType } from "../../api/client";
import { isWorkspaceless } from "../../lib/workItemHierarchy";
import { errorDetail } from "../../utils/errorDetail";
import { CreateWorkItemModal, type CreateWorkItemDraft } from "../CreateWorkItemModal";

/** Stable while loading: the modal resets its draft whenever this list changes identity. */
const NO_TICKETS: TicketSummary[] = [];

export interface CreateWorkItemRequest {
  workspaceSlug: string;
  /** Set when adding a child: the parent is fixed and the modal says so. */
  parent: { id: string; title: string; type: WorkItemType } | null;
}

interface CreateWorkItemFlowProps {
  request: CreateWorkItemRequest;
  /** The Console shows every workspace, so the operator picks one. */
  workspaceIsAll: boolean;
  /** Workspaces a new item may go in — active ones only. */
  workspaces: { slug: string; name: string }[];
  selectedTicketId: string | null;
  /** Must keep its identity across renders, for the same reason as `NO_TICKETS`. */
  ticketTree: TicketTreeNode[];
  onCreated: (ticket: TicketDetail) => void;
  onClose: () => void;
}

/**
 * Creating a work item, or a child of one, from the Console. Mount it to open
 * it: each opening starts clean.
 */
export function CreateWorkItemFlow({
  request,
  workspaceIsAll,
  workspaces,
  selectedTicketId,
  ticketTree,
  onCreated,
  onClose,
}: CreateWorkItemFlowProps) {
  const qc = useQueryClient();
  const [workspaceSlug, setWorkspaceSlug] = useState(request.workspaceSlug);
  const { parent } = request;

  const tickets = useQuery({
    queryKey: ["tickets", "create", workspaceSlug],
    queryFn: () => api.tickets({ workspace: workspaceSlug }),
    enabled: Boolean(workspaceSlug),
  });

  const create = useMutation({
    meta: { errorTitle: "Create work item" },
    mutationFn: (draft: CreateWorkItemDraft) =>
      api.createTicket({
        workspace_slug: isWorkspaceless(draft.work_item_type) ? "" : workspaceSlug,
        title: draft.title.trim(),
        work_item_type: draft.work_item_type,
        parent_ticket_id: draft.parent_ticket_id || null,
        description: draft.description.trim(),
        acceptance_criteria: draft.acceptance_criteria
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
        priority: draft.priority,
      }),
    onSuccess: (ticket) => {
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      onCreated(ticket);
      onClose();
    },
  });

  return (
    <CreateWorkItemModal
      open
      workspaceSlug={workspaceSlug}
      workspacePicker={parent ? isWorkspaceless(parent.type) : workspaceIsAll}
      workspaces={workspaces}
      onWorkspaceSlugChange={setWorkspaceSlug}
      tickets={tickets.data ?? NO_TICKETS}
      selectedTicketId={selectedTicketId}
      ticketTree={ticketTree}
      parentTicketId={parent?.id ?? null}
      parentTicketTitle={parent?.title}
      parentTicketType={parent?.type ?? null}
      lockParent={Boolean(parent)}
      isSaving={create.isPending}
      errorMessage={errorDetail(create.error)}
      onClose={onClose}
      onCreate={async (draft) => {
        await create.mutateAsync(draft);
      }}
    />
  );
}
