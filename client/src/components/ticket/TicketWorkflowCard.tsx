import { useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";

import { api } from "../../api/client";
import type { TicketDetail, WorkflowStageView } from "../../api/client";
import type { TicketWork } from "../../hooks/useTicketWork";
import { DEFAULT_ORCHESTRATION_RUNTIME } from "../../hooks/useTicketWork";
import { currentStageRunLabel, stageAgentMeta, stageKindLabel, stageStatusMeta } from "../../lib/stageDisplay";
import { canRunStage } from "../../lib/stageRunPolicy";
import { stageRowMeta, stageRunSummaries } from "../../lib/stageSummary";
import { copyStageTerminalHandoffCommand } from "../../lib/terminalCommands";
import { buildTicketTimeline } from "../../lib/ticketTimeline";
import { vineMark } from "../../lib/vine";
import { stageRouteHints, stageRouteLabel } from "../../lib/workflowTransitions";
import { StageOverflowMenu } from "../StageOverflowMenu";
import { Button } from "../ui/Button";
import { VineNode } from "../vine/VineNode";
import { runtimeSummaryLabel } from "../WorkspaceRuntimeFields";
import { WorkflowSettingsModal } from "./WorkflowSettingsModal";

import "./TicketWorkflowCard.css";

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

/**
 * "Where is this ticket in its workflow, and what do I run next?" — one row
 * per stage with how long it took or how often it has failed, each stage's
 * own menu, the current stage's agent, model and posture, and the button
 * that runs it. The ticket's settings sit behind the gear.
 */
export function TicketWorkflowCard({ ticket, work }: { ticket: TicketDetail; work: TicketWork }) {
  const headingId = useId();
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Same key as the Timeline's and the failure panel's, so the page reads the ledger once.
  const ledger = useQuery({
    queryKey: ["ticket-ledger", ticket.id],
    queryFn: () => api.ticketLedger(ticket.id),
    refetchInterval: work.hasActiveRun ? 2000 : false,
  });
  const summaries = useMemo(
    () => stageRunSummaries(buildTicketTimeline(ledger.data?.visits ?? [], []).visits),
    [ledger.data?.visits],
  );

  const stages = ticket.stages;
  const doneCount = stages.filter((s) => s.status === "done" || s.status === "wont_do").length;
  const current = stages.find((s) => s.key === ticket.workflow_stage_key);
  const requestStageRun = (stageKey: string) => work.setRunConfirmStageKey(stageKey);

  return (
    <section className="twf" aria-labelledby={headingId}>
      <div className="twf-head">
        <h2 id={headingId} className="twf-title">
          Workflow
        </h2>
        {stages.length > 0 ? (
          <span className="twf-count" title={`${doneCount} of ${stages.length} stages done`}>
            {doneCount}/{stages.length}
          </span>
        ) : null}
        <Button
          variant="secondary"
          compact
          className="twf-gear"
          aria-label="Workflow settings"
          title="Workflow settings: template, branch, model and posture"
          aria-haspopup="dialog"
          aria-expanded={settingsOpen}
          onClick={() => setSettingsOpen(true)}
        >
          <GearIcon />
        </Button>
      </div>

      {stages.length === 0 ? (
        <p className="twf-empty">
          No workflow yet. Choose a template in{" "}
          <Button variant="plain" className="twf-link" onClick={() => setSettingsOpen(true)}>
            Workflow settings
          </Button>{" "}
          to give this ticket stages to run.
        </p>
      ) : (
        <ol className="twf-stages" aria-label="Stages">
          {stages.map((stage, index) => (
            <StageRow
              key={stage.key}
              ticket={ticket}
              work={work}
              stage={stage}
              index={index}
              last={index === stages.length - 1}
              meta={stageRowMeta(stage, summaries.get(stage.key))}
              failing={summaries.get(stage.key)?.failing ?? false}
              onRun={requestStageRun}
            />
          ))}
        </ol>
      )}

      {current ? (
        <CurrentStage
          ticket={ticket}
          work={work}
          stage={current}
          failing={summaries.get(current.key)?.failing ?? false}
          onRun={requestStageRun}
        />
      ) : null}

      <WorkflowSettingsModal
        open={settingsOpen}
        ticket={ticket}
        work={work}
        onClose={() => setSettingsOpen(false)}
        onOpenModel={() => {
          setSettingsOpen(false);
          work.setTicketModelModalOpen(true);
        }}
      />
    </section>
  );
}

function StageRow({
  ticket,
  work,
  stage,
  index,
  last,
  meta,
  failing,
  onRun,
}: {
  ticket: TicketDetail;
  work: TicketWork;
  stage: WorkflowStageView;
  index: number;
  last: boolean;
  meta: string;
  failing: boolean;
  onRun: (stageKey: string) => void;
}) {
  const isCurrent = stage.key === ticket.workflow_stage_key;
  const runCheck = canRunStage(ticket, stage);
  const isRunning = work.isStageRunning(stage.key);
  const kind = stageKindLabel(stage);
  const className = [
    "twf-stage",
    `twf-stage--${stage.status}`,
    isCurrent ? "is-current" : "",
    failing ? "is-failing" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <li className={className} aria-current={isCurrent ? "step" : undefined}>
      <VineNode index={index} mark={vineMark(stage.status, isCurrent)} last={last} />
      <span className="twf-name" title={kind ?? undefined}>
        {stage.name}
      </span>
      {/* The vine mark is decoration; this is what it says. */}
      <span className="twf-sr">
        {failing ? "Failing" : stageStatusMeta(stage.status).label}
        {isCurrent ? ", current stage" : ""}
      </span>
      {meta ? <span className="twf-meta">{meta}</span> : null}
      <StageOverflowMenu
        ticket={ticket}
        stage={stage}
        runCheck={runCheck}
        isRunning={isRunning}
        workflowBusy={work.workflowBusy || work.routeWorkflow.isPending || work.patchStageWorkflow.isPending}
        onRun={onRun}
        copyTerminalPending={work.copyTerminalStageKey !== null}
        onCopyTerminal={() => {
          if (work.copyTerminalStageKey) return;
          work.setCopyTerminalStageKey(stage.key);
          void copyStageTerminalHandoffCommand(ticket, stage).finally(() => work.setCopyTerminalStageKey(null));
        }}
        onSetCursor={(stageKey) =>
          work.patchStageWorkflow.mutate({ workflow_stage_key: stageKey, workflow_stage_status: "pending" })
        }
        onRouteUpstream={(fromStageKey, toStageKey, nextAgent) =>
          work.routeWorkflow.mutate({ from_stage_key: fromStageKey, next_stage_key: toStageKey, next_agent: nextAgent })
        }
        onStageStatus={(stageKey, status) => work.patchStageWorkflow.mutate({ stage_key: stageKey, stage_status: status })}
        onEditState={() => work.setStateModalOpen(true)}
      />
    </li>
  );
}

/** The stage the cursor is on: who runs it, how, where it routes, and the button that runs it. */
function CurrentStage({
  ticket,
  work,
  stage,
  failing,
  onRun,
}: {
  ticket: TicketDetail;
  work: TicketWork;
  stage: WorkflowStageView;
  failing: boolean;
  onRun: (stageKey: string) => void;
}) {
  const { agent, skill } = stageAgentMeta(stage);
  const routes = stageRouteHints(stage.key, ticket.workflow_transitions ?? [], ticket.stages);
  const kind = stageKindLabel(stage);
  const runCheck = canRunStage(ticket, stage);
  const isRunning = work.isStageRunning(stage.key);
  const busy = work.workflowBusy || work.startRun.isPending;
  const posture = ticket.resolved_compatibility_posture || "—";

  return (
    <>
      <dl className="twf-set">
        <dt>Current</dt>
        <dd>
          {stage.name}
          {kind ? <span className="twf-set-note"> · {kind}</span> : null}
        </dd>
        {agent ? (
          <>
            <dt>Agent</dt>
            <dd className="twf-mono">{skill ? `${agent} · ${skill}` : agent}</dd>
          </>
        ) : null}
        <dt>Model</dt>
        <dd>{runtimeSummaryLabel(ticket.orchestration_runtime ?? DEFAULT_ORCHESTRATION_RUNTIME, work.runtimeOptions.data)}</dd>
        <dt>Posture</dt>
        <dd>
          {posture}
          {ticket.compatibility_posture_source ? (
            <span className="twf-set-note"> · {ticket.compatibility_posture_source}</span>
          ) : null}
        </dd>
        {routes.length > 0 ? (
          <>
            <dt>Routes</dt>
            <dd>
              <ul className="twf-routes">
                {routes.map((hint) => (
                  <li
                    key={`${hint.when}-${hint.targetKey}`}
                    className={hint.upstream || hint.when === "reject" ? "is-upstream" : undefined}
                  >
                    {stageRouteLabel(hint)}
                  </li>
                ))}
              </ul>
            </dd>
          </>
        ) : null}
      </dl>
      {stage.note ? (
        <p className="twf-note" data-status={stage.status}>
          {stage.note}
        </p>
      ) : null}
      <Button
        variant={failing ? "primary" : "secondary"}
        className="twf-run"
        disabled={!runCheck.allowed || busy || isRunning}
        title={runCheck.reason}
        onClick={() => onRun(stage.key)}
      >
        {failing && !isRunning ? "Retry stage" : currentStageRunLabel(stage, isRunning)}
      </Button>
    </>
  );
}
