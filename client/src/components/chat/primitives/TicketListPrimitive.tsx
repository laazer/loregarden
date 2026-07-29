import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { TicketTreeNode } from "../../../api/types";
import { TicketTree } from "../../TicketTree";
import type { TicketListPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";
import { PlayButton, StopButton } from "./RunControlButton";
import { toTicketTreeNode } from "./ticketTreeNodes";
import { ticketIsRunning, useRunControls } from "./useRunControls";

function filterTree(nodes: TicketTreeNode[], ids: Set<string>): TicketTreeNode[] {
  const out: TicketTreeNode[] = [];
  for (const node of nodes) {
    const kids = filterTree(node.children, ids);
    if (ids.has(node.id) || kids.length > 0) {
      out.push({ ...node, children: kids });
    }
  }
  return out;
}

export function TicketListPrimitive({ part }: { part: TicketListPart }) {
  const parentId = part.parent_ticket_id ?? undefined;
  const controls = useRunControls(parentId);
  const treeQuery = useQuery({
    queryKey: ["ticket-tree", parentId ?? "all", part.ticket_ids ?? []],
    queryFn: async () => {
      if (parentId) {
        const children = await api.tickets({ parent_ticket_id: parentId });
        return children.map(toTicketTreeNode);
      }
      return api.ticketTree({});
    },
    refetchInterval: 5000,
  });

  const idSet = useMemo(
    () => new Set(part.ticket_ids ?? []),
    [part.ticket_ids],
  );
  const nodes = useMemo(() => {
    const raw = treeQuery.data ?? [];
    if (!part.ticket_ids?.length || parentId) return raw;
    return filterTree(raw, idSet);
  }, [treeQuery.data, part.ticket_ids, parentId, idSet]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());

  const parentQuery = useQuery({
    queryKey: ["ticket", parentId],
    queryFn: () => api.ticket(parentId!),
    enabled: Boolean(parentId),
  });
  const running = ticketIsRunning(parentQuery.data?.workflow_stage_status);

  return (
    <PrimitiveCard
      title={part.title ?? parentQuery.data?.title ?? "Ticket list"}
      subtitle={parentId ? parentQuery.data?.external_id : `${nodes.length} tickets`}
      loading={treeQuery.isLoading}
      error={
        treeQuery.error
          ? treeQuery.error instanceof Error
            ? treeQuery.error.message
            : "Failed to load"
          : null
      }
      resourceAction={
        parentId ? <OpenTicketButton ticketId={parentId} label="Open parent ticket" /> : null
      }
      actions={
        parentId ? (
          running ? (
            <StopButton disabled={controls.isStopping} onClick={() => void controls.stop()} />
          ) : (
            <PlayButton disabled={controls.isStarting} onClick={() => void controls.start()} />
          )
        ) : null
      }
    >
      <TicketTree
        nodes={nodes}
        selectedId={selectedId}
        expandedIds={expandedIds}
        onSelect={setSelectedId}
        renderRowAction={(node) => (
          <OpenTicketButton ticketId={node.id} compact label={`Open ${node.title}`} />
        )}
        onToggle={(id) =>
          setExpandedIds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
          })
        }
      />
    </PrimitiveCard>
  );
}
