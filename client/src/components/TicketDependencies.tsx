import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TicketDependencyRef, TicketDetail } from "../api/client";

interface TicketDependenciesProps {
  ticket: TicketDetail;
}

/** View and manage the tickets this one waits for (and what waits on it). */
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
    mutationFn: (dependsOnId: string) => api.removeDependency(ticket.id, dependsOnId),
    onSuccess: invalidate,
  });

  const submit = () => {
    const trimmed = value.trim();
    if (trimmed) addDep.mutate(trimmed);
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <div className="state-label workflow-lifecycle-label" style={{ marginBottom: 6 }}>
        Dependencies
      </div>

      {dependencies.length === 0 ? (
        <div style={{ fontSize: 13, marginBottom: 8, color: "var(--fg2)" }}>
          Waits for nothing.
        </div>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: "0 0 8px" }}>
          {dependencies.map((dep) => (
            <li
              key={dep.id}
              style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}
            >
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
            </li>
          ))}
        </ul>
      )}

      <div style={{ display: "flex", gap: 6 }}>
        <input
          type="text"
          value={value}
          placeholder="Wait for ticket (id or external id)…"
          onChange={(e) => {
            setValue(e.target.value);
            if (error) setError("");
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          style={{ flex: 1 }}
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
        <div style={{ fontSize: 12, marginTop: 4, color: "var(--red)" }}>{error}</div>
      )}

      {dependents.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 12, marginBottom: 4, color: "var(--fg2)" }}>
            Blocking {dependents.length} ticket{dependents.length === 1 ? "" : "s"}:
          </div>
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {dependents.map((dep) => (
              <li key={dep.id} style={{ marginBottom: 2 }}>
                <DependencyLabel dep={dep} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function DependencyLabel({ dep }: { dep: TicketDependencyRef }) {
  return (
    <span style={{ fontSize: 13 }} title={`${dep.external_id} — ${dep.state}`}>
      <span className="count-pill">{dep.external_id}</span> {dep.title}
      {dep.is_integration_review && (
        <span className="count-pill" style={{ marginLeft: 6 }}>
          review
        </span>
      )}
    </span>
  );
}
