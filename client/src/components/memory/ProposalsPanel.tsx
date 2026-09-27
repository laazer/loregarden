/**
 * Maintenance proposals: what looks wrong in the graph, for a person to decide.
 *
 * The server proposes and never acts (`services/memory_curation.py`). Every
 * action here is one click to open a confirm step and one to commit, and each
 * records who and why in the learning's history. Nothing is merged, renamed or
 * withdrawn because a heuristic thought it should be.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { MemoryProposal, ProposalKind } from "../../api/memoryApi";
import { describeError, pushToast } from "../../state/toastStore";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { ReasonConfirmDialog } from "./ReasonConfirmDialog";

const KIND_LABELS: Record<ProposalKind, { label: string; action: string }> = {
  generic_title: { label: "Titled by its ticket", action: "Retitle" },
  near_duplicate: { label: "Looks like a duplicate", action: "Merge" },
  contested: { label: "Unresolved contradiction", action: "Resolve" },
  cold: { label: "Gone cold", action: "Discredit" },
};

type Pending = { proposal: MemoryProposal; choice: number; title: string };

function ProposalRow({
  proposal,
  onOpen,
  onAct,
}: {
  proposal: MemoryProposal;
  onOpen: (nodeId: string) => void;
  onAct: () => void;
}) {
  const { label, action } = KIND_LABELS[proposal.kind];
  return (
    <li className={`memory-proposal memory-proposal--${proposal.kind}`}>
      <div className="memory-proposal-head">
        <span className="state-label">
          {label}
          {proposal.similarity !== null ? ` · ${Math.round(proposal.similarity * 100)}% alike` : ""}
        </span>
        <button type="button" className="btn-secondary btn-compact" onClick={onAct}>
          {action}…
        </button>
      </div>
      <div className="memory-proposal-titles">
        {proposal.node_ids.map((id, index) => (
          <button key={id} type="button" className="memory-link-button" onClick={() => onOpen(id)}>
            {proposal.titles[index]}
          </button>
        ))}
      </div>
      <p className="memory-muted">{proposal.reason}</p>
    </li>
  );
}

function ChoiceOfTwo({
  legend,
  pending,
  onChange,
}: {
  legend: string;
  pending: Pending;
  onChange: (choice: number) => void;
}) {
  return (
    <fieldset className="memory-choice">
      <legend>{legend}</legend>
      {pending.proposal.titles.map((title, index) => (
        <label key={pending.proposal.node_ids[index]}>
          <input
            type="radio"
            name="memory-choice"
            checked={pending.choice === index}
            onChange={() => onChange(index)}
          />
          <span>{title}</span>
        </label>
      ))}
    </fieldset>
  );
}

export function ProposalsPanel({
  workspaceSlug,
  onOpen,
}: {
  workspaceSlug: string;
  onOpen: (nodeId: string) => void;
}) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<Pending | null>(null);
  const proposals = useQuery({
    queryKey: ["memory-proposals", workspaceSlug],
    queryFn: () => api.memoryProposals(workspaceSlug),
    meta: { errorTitle: "Load maintenance proposals" },
  });

  const act = useMutation({
    meta: { errorTitle: "Apply maintenance action" },
    mutationFn: async ({ step, reason }: { step: Pending; reason: string }) => {
      const { proposal, choice } = step;
      const chosen = proposal.node_ids[choice];
      const other = proposal.node_ids[1 - choice];
      if (proposal.kind === "generic_title") {
        return api.retitleMemoryNode(chosen, {
          workspace_slug: workspaceSlug,
          title: step.title.trim(),
          reason,
        });
      }
      if (proposal.kind === "near_duplicate") {
        return api.mergeMemoryNodes(chosen, {
          workspace_slug: workspaceSlug,
          absorbed_id: other,
          reason,
        });
      }
      if (proposal.kind === "contested") {
        return api.createMemoryRelation({
          workspace_slug: workspaceSlug,
          source_id: chosen,
          target_id: other,
          relation_type: "supersedes",
        });
      }
      return api.setMemoryNodeDiscredited(chosen, {
        workspace_slug: workspaceSlug,
        discredited: true,
        reason,
      });
    },
    onSuccess: (_result, { step }) => {
      for (const key of [
        "memory-proposals",
        "memory-nodes",
        "memory-node",
        "memory-graph-health",
      ]) {
        void qc.invalidateQueries({ queryKey: [key, workspaceSlug] });
      }
      setPending(null);
      pushToast({
        tone: "success",
        title: `${KIND_LABELS[step.proposal.kind].action} applied`,
        message: step.proposal.titles[step.choice],
      });
    },
  });

  let body;
  if (proposals.isLoading) {
    body = <PaneSkeleton variant="list" rows={3} label="Looking for maintenance work…" />;
  } else if (!proposals.data) {
    body = (
      <p className="memory-error" role="alert">
        Could not load proposals: {describeError(proposals.error, "the request failed")}.
      </p>
    );
  } else if (proposals.data.length === 0) {
    body = (
      <p className="memory-empty">
        Nothing to decide: no ticket-titled learnings, likely duplicates, unresolved contradictions
        or cold learnings in {workspaceSlug}.
      </p>
    );
  } else {
    body = (
      <ul className="memory-proposals" aria-label="Maintenance proposals">
        {proposals.data.map((proposal) => (
          <ProposalRow
            key={`${proposal.kind}:${proposal.node_ids.join(":")}`}
            proposal={proposal}
            onOpen={onOpen}
            onAct={() => setPending({ proposal, choice: 0, title: proposal.suggested_title ?? "" })}
          />
        ))}
      </ul>
    );
  }

  const kind = pending?.proposal.kind;
  const titleMissing = kind === "generic_title" && !pending?.title.trim();
  return (
    <section className="memory-panel" aria-labelledby="memory-proposals-title">
      <header className="memory-panel-header">
        <h2 id="memory-proposals-title" className="memory-panel-title">
          Maintenance
        </h2>
      </header>
      {body}
      <ReasonConfirmDialog
        key={pending ? `${pending.proposal.kind}:${pending.proposal.node_ids.join(":")}` : "closed"}
        open={pending !== null}
        title={
          kind === "generic_title"
            ? "Retitle this learning?"
            : kind === "near_duplicate"
              ? "Merge these learnings?"
              : kind === "contested"
                ? "Resolve this contradiction?"
                : "Withdraw this learning?"
        }
        description={
          kind === "generic_title"
            ? "The old title is kept as an alias, so anything that found it by that name still does."
            : kind === "near_duplicate"
              ? "The other learning's body, names and relations move to the one you keep, its observed outcomes move with them, and it is withdrawn. Nothing it said is lost."
              : kind === "contested"
                ? "The one you pick supersedes the other. Agents keep seeing both, the superseded one labelled and ranked second. Pick only if the evidence settled it."
                : "Agents stop being briefed with it. It stays in the record and can be restored."
        }
        reasonLabel={kind === "contested" ? undefined : "Reason (stored with the change)"}
        confirmLabel={pending ? KIND_LABELS[pending.proposal.kind].action : ""}
        danger={kind === "cold"}
        isSaving={act.isPending}
        confirmBlocked={titleMissing}
        onClose={() => setPending(null)}
        onConfirm={(reason) => pending && act.mutate({ step: pending, reason })}
      >
        {pending && kind === "generic_title" && (
          <label className="memory-field">
            <span>New title</span>
            <input
              type="text"
              value={pending.title}
              onChange={(event) => setPending({ ...pending, title: event.target.value })}
            />
          </label>
        )}
        {pending && kind === "near_duplicate" && (
          <ChoiceOfTwo
            legend="Keep"
            pending={pending}
            onChange={(choice) => setPending({ ...pending, choice })}
          />
        )}
        {pending && kind === "contested" && (
          <ChoiceOfTwo
            legend="Current position"
            pending={pending}
            onChange={(choice) => setPending({ ...pending, choice })}
          />
        )}
      </ReasonConfirmDialog>
    </section>
  );
}
