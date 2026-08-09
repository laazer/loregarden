import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TicketDetail } from "../api/client";

import { TicketRefLabel } from "./TicketRefLabel";

interface TicketRelationsProps {
  ticket: TicketDetail;
}

/** View and manage the tickets linked to this one for context.
 *
 * Deliberately a separate card from TicketDependencies: a relation is symmetric
 * and never blocks, so collapsing the two into one list would invite reading a
 * "see also" as an ordering constraint. */
export function TicketRelations({ ticket }: TicketRelationsProps) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  const related = ticket.related ?? [];

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["ticket", ticket.id] });
  };

  const addRelation = useMutation({
    meta: { errorTitle: "Add related ticket" },
    mutationFn: (relatedTo: string) => api.addRelation(ticket.id, relatedTo),
    onSuccess: () => {
      setValue("");
      setError("");
      invalidate();
    },
    onError: (err) =>
      setError(err instanceof Error && err.message ? err.message : "Could not add related ticket"),
  });

  const removeRelation = useMutation({
    meta: { errorTitle: "Remove related ticket" },
    mutationFn: (relatedId: string) => api.removeRelation(ticket.id, relatedId),
    onSuccess: invalidate,
  });

  const submit = () => {
    const trimmed = value.trim();
    if (trimmed) addRelation.mutate(trimmed);
  };

  return (
    <div className="state-card">
      <div className="state-label">Related</div>

      {related.length === 0 ? (
        <p className="modal-hint" style={{ marginTop: 4 }}>
          Nothing related yet.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
          {related.map((rel) => (
            <div key={rel.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <TicketRefLabel ticket={rel} />
              <button
                type="button"
                className="btn-secondary btn-compact"
                aria-label={`Remove related ticket ${rel.external_id}`}
                title="Remove related ticket"
                disabled={removeRelation.isPending}
                onClick={() => removeRelation.mutate(rel.id)}
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
          placeholder="Relate to ticket (id or external id)…"
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
          disabled={!value.trim() || addRelation.isPending}
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
      <p className="modal-hint" style={{ marginTop: 6 }}>
        Context only — related tickets do not wait for each other.
      </p>
    </div>
  );
}
