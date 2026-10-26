import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TicketDetail } from "../api/client";
import type { TicketDependencyRef } from "../api/types";
import { peekTicket } from "../state/ticketPeekStore";
import { ticketStateLabel } from "../lib/ticketStates";
import { describeError } from "../state/toastStore";

import { TicketRefLabel } from "./TicketRefLabel";
import { Button } from "./ui/Button";
import { PrerequisitePicker } from "./waiting/PrerequisitePicker";
import "./waiting/WaitingOn.css";

interface TicketDependenciesProps {
  ticket: TicketDetail;
}

/** Where a prerequisite stands, as dispatch judges it: whether it still holds this ticket, and why. */
function prerequisiteStatus(dep: TicketDependencyRef): { text: string; holding: boolean } {
  if (dep.overridden) return { text: "started past it", holding: false };
  if (dep.unmet_reason === "not_landed") {
    return { text: `done, not landed on ${dep.landing_target || "the target"}`, holding: true };
  }
  if (dep.unmet_reason === "not_done") {
    return { text: `waiting · ${ticketStateLabel(dep.state).toLowerCase()}`, holding: true };
  }
  return { text: "met", holding: false };
}

/**
 * The tickets this one waits for, which of them still hold it, and what waits on it.
 *
 * Each row opens its ticket and says whether it still holds this one: not done,
 * done but not landed on this ticket's target, met, or started past. Before,
 * rows were unclickable labels that said none of it, so "is this the one
 * holding me up?" meant opening every prerequisite in turn.
 *
 * Removing an edge asks first: an edge is what holds a dispatch, and one removed
 * by a slip lets the ticket start into a tree without its prerequisite's work.
 */
export function TicketDependencies({ ticket }: TicketDependenciesProps) {
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState<string | null>(null);

  const dependencies = ticket.dependencies ?? [];
  const dependents = ticket.dependents ?? [];
  const holdingCount = dependencies.filter((dep) => prerequisiteStatus(dep).holding).length;

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["ticket", ticket.id] });
    qc.invalidateQueries({ queryKey: ["ticket-tree"] });
  };

  const addDep = useMutation({
    meta: { errorTitle: "Add dependency" },
    mutationFn: (dependsOn: string) => api.addDependency(ticket.id, dependsOn),
    onSuccess: () => {
      setError("");
      invalidate();
    },
    onError: (err) => setError(describeError(err, "Could not add the dependency")),
  });

  const removeDep = useMutation({
    meta: { errorTitle: "Remove dependency" },
    mutationFn: (dependsOnId: string) => api.removeDependency(ticket.id, dependsOnId),
    onSuccess: () => {
      setRemoving(null);
      invalidate();
    },
  });

  return (
    <div className="state-card">
      <div className="state-label">
        Dependencies
        {dependencies.length > 0 ? (
          <span className="prereq-status">
            {" "}
            · {holdingCount === 0 ? "none holding it" : `${holdingCount} holding it`}
          </span>
        ) : null}
      </div>

      {dependencies.length === 0 ? (
        <p className="modal-hint" style={{ marginTop: 4 }}>
          Waits for nothing. Add a ticket below to hold this one until it is done and landed.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
          {dependencies.map((dep) => {
            const status = prerequisiteStatus(dep);
            const elsewhere =
              dep.workspace_slug && dep.workspace_slug !== ticket.workspace_slug
                ? `${dep.workspace_slug}: `
                : "";
            return (
              <div key={dep.id} className="prereq-row">
                <Button
                  variant="plain"
                  className="prereq-row-title"
                  title={`${dep.title} — ${ticketStateLabel(dep.state)}`}
                  onClick={() => peekTicket(dep.id)}
                >
                  <span className="count-pill">
                    {elsewhere}
                    {dep.external_id}
                  </span>{" "}
                  {dep.title}
                </Button>
                <span className={`prereq-status${status.holding ? " prereq-status--holding" : ""}`}>
                  {status.text}
                </span>
                {removing === dep.id ? (
                  <>
                    <Button
                      variant="secondary"
                      className="btn-compact"
                      disabled={removeDep.isPending}
                      onClick={() => removeDep.mutate(dep.id)}
                    >
                      {removeDep.isPending ? "Removing…" : "Remove"}
                    </Button>
                    <Button
                      variant="secondary"
                      className="btn-compact"
                      disabled={removeDep.isPending}
                      onClick={() => setRemoving(null)}
                    >
                      Keep
                    </Button>
                  </>
                ) : (
                  <Button
                    variant="secondary"
                    className="btn-compact"
                    aria-label={`Stop waiting for ${dep.external_id}`}
                    title="Stop waiting for this ticket"
                    onClick={() => setRemoving(dep.id)}
                  >
                    ✕
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}

      <PrerequisitePicker
        ticketId={ticket.id}
        exclude={dependencies.map((dep) => dep.id)}
        pending={addDep.isPending}
        onPick={(reference) => addDep.mutate(reference)}
      />
      {error && (
        <p className="modal-hint" role="alert" style={{ marginTop: 4, color: "var(--red)" }}>
          {error}
        </p>
      )}

      {dependents.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: "var(--fs-micro)", color: "var(--txm)" }}>
            Blocking {dependents.length} ticket{dependents.length === 1 ? "" : "s"}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
            {dependents.map((dep) => (
              <Button
                key={dep.id}
                variant="plain"
                className="prereq-row-title"
                aria-label={`Open ${dep.external_id}: ${dep.title}`}
                onClick={() => peekTicket(dep.id)}
              >
                <TicketRefLabel ticket={dep} />
              </Button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
