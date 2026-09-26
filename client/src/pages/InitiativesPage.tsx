import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { CreateInitiativeForm } from "../components/initiatives/CreateInitiativeForm";
import { InitiativeCard } from "../components/initiatives/InitiativeCard";
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
    mutationFn: (draft: { title: string; description: string }) => api.createInitiative(draft),
    onSuccess: () => {
      setCreating(false);
      refresh();
    },
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
          <div className="queue-page-empty">
            <div>
              <p>
                No initiatives yet. An initiative groups milestones from any workspace under one
                goal and rolls up their progress.
              </p>
              <button type="button" className="btn-primary" onClick={() => setCreating(true)}>
                Create the first initiative
              </button>
            </div>
          </div>
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
    </div>
  );
}
