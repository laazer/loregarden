import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { CreateInitiativeForm } from "../components/initiatives/CreateInitiativeForm";
import { InitiativeCard } from "../components/initiatives/InitiativeCard";
import { UnassignedMilestones } from "../components/initiatives/UnassignedMilestones";
import { PageTopbar } from "../components/TopbarPageSlot";
import { describeError } from "../state/toastStore";
import "../components/initiatives/Initiatives.css";

const INITIATIVES_KEY = ["initiatives"] as const;
const ATTACHABLE_KEY = ["initiatives", "attachable-milestones"] as const;

/**
 * Initiatives span workspaces, so this page ignores the workspace switcher: an
 * initiative shows every milestone it owns, whichever repository it lives in.
 */
export function InitiativesPage() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());

  const initiatives = useQuery({
    queryKey: INITIATIVES_KEY,
    queryFn: api.initiatives,
    meta: { errorTitle: "Load initiatives" },
  });
  const attachable = useQuery({
    queryKey: ATTACHABLE_KEY,
    queryFn: api.attachableMilestones,
    meta: { errorTitle: "Load milestones" },
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: INITIATIVES_KEY });
    // The Console's tree and lists show the same parent links.
    void qc.invalidateQueries({ queryKey: ["ticket-tree"] });
    void qc.invalidateQueries({ queryKey: ["tickets"] });
  };

  const create = useMutation({
    meta: { errorTitle: "Create initiative" },
    // Attach sequentially: each is a PATCH that re-derives the parent's rollup,
    // and SQLite serialises the writes anyway. A failure part-way leaves the
    // initiative with the milestones attached so far, and the error toast (via
    // `meta.errorTitle`) says which one stopped it.
    mutationFn: async (draft: { title: string; description: string }) => {
      const created = await api.createInitiative(draft);
      for (const milestoneId of picked) {
        const milestone = attachable.data?.find((m) => m.id === milestoneId);
        try {
          await api.setMilestoneInitiative(milestoneId, created.id);
        } catch (error) {
          throw new Error(
            `Created “${draft.title}”, but attaching ${milestone?.external_id ?? milestoneId} failed: ${describeError(error)}`,
          );
        }
      }
      return created;
    },
    onSuccess: () => {
      setCreating(false);
      setPicked(new Set());
    },
    onSettled: refresh,
  });

  const reparent = useMutation({
    meta: { errorTitle: "Update initiative milestones" },
    mutationFn: ({ milestoneId, initiativeId }: { milestoneId: string; initiativeId: string | null }) =>
      api.setMilestoneInitiative(milestoneId, initiativeId),
    onMutate: ({ milestoneId }) => setBusyId(milestoneId),
    onSettled: () => {
      setBusyId(null);
      refresh();
    },
  });

  const remove = useMutation({
    meta: { errorTitle: "Delete initiative" },
    mutationFn: (id: string) => api.deleteTicket(id),
    onMutate: (id) => setBusyId(id),
    onSettled: () => {
      setBusyId(null);
      refresh();
    },
  });

  const list = initiatives.data ?? [];

  return (
    <div className="initiatives-page">
      <PageTopbar title="Initiatives">
        <button
          type="button"
          className="btn-primary"
          disabled={creating}
          onClick={() => setCreating(true)}
        >
          New initiative
        </button>
      </PageTopbar>

      {creating && (
        <CreateInitiativeForm
          milestones={(attachable.data ?? []).filter((m) => picked.has(m.id))}
          isSaving={create.isPending}
          onCreate={(draft) => create.mutateAsync(draft)}
          onCancel={() => setCreating(false)}
        />
      )}

      {initiatives.isPending ? (
        <div aria-busy="true" aria-label="Loading initiatives">
          <div className="initiative-skeleton" />
        </div>
      ) : initiatives.isError ? (
        <div className="queue-page-empty" role="alert">
          <div>
            <p>Initiatives could not be loaded: {describeError(initiatives.error)}</p>
            <button type="button" className="btn-secondary" onClick={() => void initiatives.refetch()}>
              Retry
            </button>
          </div>
        </div>
      ) : list.length === 0 ? (
        !creating && (
          <p className="initiative-intro">
            No initiatives yet. An initiative is one goal that spans workspaces — its milestones can live in any repo,
            and their progress rolls up here. Start from the milestones below, or{" "}
            <button type="button" className="initiative-link-btn" onClick={() => setCreating(true)}>
              create an empty one
            </button>
            .
          </p>
        )
      ) : (
        list.map((initiative) => (
          <InitiativeCard
            key={initiative.id}
            initiative={initiative}
            attachable={attachable.data ?? []}
            busyId={busyId}
            onAttach={(milestoneId) => reparent.mutate({ milestoneId, initiativeId: initiative.id })}
            onDetach={(milestoneId) => reparent.mutate({ milestoneId, initiativeId: null })}
            onDelete={() => remove.mutate(initiative.id)}
          />
        ))
      )}

      {attachable.isError ? (
        <p className="initiative-hint" role="alert">
          Milestones could not be loaded: {describeError(attachable.error)}{" "}
          <button type="button" className="initiative-link-btn" onClick={() => void attachable.refetch()}>
            Retry
          </button>
        </p>
      ) : attachable.isPending ? (
        <div className="initiative-skeleton" aria-busy="true" aria-label="Loading milestones" />
      ) : (
        <UnassignedMilestones
          milestones={attachable.data}
          selected={picked}
          disabled={create.isPending || busyId !== null}
          onToggle={(id) =>
            setPicked((current) => {
              const next = new Set(current);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            })
          }
          // The form's title field autofocuses on mount, which scrolls it into view.
          onGroup={() => setCreating(true)}
        />
      )}
    </div>
  );
}
