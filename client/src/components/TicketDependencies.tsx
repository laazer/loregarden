import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TicketDependencyRef, TicketDetail } from "../api/client";

interface TicketDependenciesProps {
  ticket: TicketDetail;
}

/** View and manage the tickets this one waits for (and what waits on it).
 * Styled as a modal state-card to sit alongside the other ticket-details sections. */
export function TicketDependencies({ ticket }: TicketDependenciesProps) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  const dependencies = ticket.dependencies ?? [];
  const dependents = ticket.dependents ?? [];

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["ticket", ticket.id] });
    qc.invalidateQueries({ queryKey: ["ticket-tree"] });
  };

  const addDep = useMutation({
    meta: { errorTitle: "Add dependency" },
    mutationFn: (dependsOn: string) => api.addDependency(ticket.id, dependsOn),
    onSuccess: () => {
      setValue("");
      setError("");
      invalidate();
    },
    onError: (err) =>
      setError(err instanceof Error && err.message ? err.message : "Could not add dependency"),
  });

  const removeDep = useMutation({
    meta: { errorTitle: "Remove dependency" },
    mutationFn: (dependsOnId: string) => api.removeDependency(ticket.id, dependsOnId),
    onSuccess: invalidate,
  });

  const submit = () => {
    const trimmed = value.trim();
    if (trimmed) addDep.mutate(trimmed);
  };

  return (
    <div className="state-card">
      <div className="state-label">Dependencies</div>

      {dependencies.length === 0 ? (
        <p className="modal-hint" style={{ marginTop: 4 }}>
          Waits for nothing.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
          {dependencies.map((dep) => (
            <div key={dep.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <DependencyLabel dep={dep} />
              <button
                type="button"
                className="btn-secondary btn-compact"
                title="Remove dependency"
                disabled={removeDep.isPending}
                onClick={() => removeDep.mutate(dep.id)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <input
          type="text"
          className="btn-secondary filter-select"
          style={{ flex: 1, fontSize: 13 }}
          value={value}
          placeholder="Wait for ticket (id or external id)…"
          onChange={(e) => {
            setValue(e.target.value);
            if (error) setError("");
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={!value.trim() || addDep.isPending}
          onClick={submit}
        >
          Add
        </button>
      </div>
      {error && (
        <p className="modal-hint" style={{ marginTop: 4, color: "var(--red)" }}>
          {error}
        </p>
      )}

      {dependents.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 11, color: "var(--txm)" }}>
            Blocking {dependents.length} ticket{dependents.length === 1 ? "" : "s"}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
            {dependents.map((dep) => (
              <DependencyLabel key={dep.id} dep={dep} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DependencyLabel({ dep }: { dep: TicketDependencyRef }) {
  return (
    <span style={{ fontSize: 13, color: "var(--tx)" }} title={`${dep.external_id} — ${dep.state}`}>
      <span className="count-pill">{dep.external_id}</span> {dep.title}
      {dep.is_integration_review && (
        <span className="count-pill" style={{ marginLeft: 6 }}>
          review
        </span>
      )}
    </span>
  );
}
