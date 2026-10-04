import { Link } from "react-router-dom";

import { studioAgentPath, studioWorkflowPath } from "../../../lib/appNavigation";
import { GATE_CONTROL_COPY } from "./gateControlKinds";
import type { GateControl } from "./gateControlModel";

/**
 * One control, in six sections: what it is, what it does, where it is stored,
 * what it covers, its value, and where to change it. Every string comes from
 * the control (built from the descriptor table), so the copy for a kind lives
 * in one place (lg-gate-studio-863, 631's R9).
 *
 * Only transition commands are edited here — the caller mounts the editor
 * below this. The other kinds link to the studio that owns them.
 */
export function GateControlDetail({
  control,
  workflowSlug,
}: {
  control: GateControl;
  workflowSlug: string | null;
}) {
  const copy = GATE_CONTROL_COPY[control.kind];
  return (
    <article className="gate-card gate-studio-detail" aria-labelledby="gate-detail-title">
      <header className="gate-card-head">
        <div>
          <div className="gate-studio-kind">{copy.label}</div>
          <h2 id="gate-detail-title">{control.title}</h2>
        </div>
        <span className={`gate-studio-tone gate-studio-tone--${copy.tone}`}>{control.enforcementBadge}</span>
      </header>
      <dl className="gate-studio-facts">
        <dt>What this does</dt>
        <dd>{control.explanation}</dd>
        <dt>Stored in</dt>
        <dd>{control.storedIn}</dd>
        <dt>Scope</dt>
        <dd>{control.scope}</dd>
        <dt>Value</dt>
        <dd className="gate-mono">{control.value}</dd>
        <dt>Where to change it</dt>
        <dd>
          <WhereToChange control={control} workflowSlug={workflowSlug} />
        </dd>
      </dl>
    </article>
  );
}

function WhereToChange({ control, workflowSlug }: { control: GateControl; workflowSlug: string | null }) {
  if (control.kind === "workspace_transition_command") {
    return <>In the editor below — save applies it to every transition in this workspace.</>;
  }
  if (control.kind === "agent_handoff_check" && control.sourceRef.agentSlug) {
    return (
      <Link to={studioAgentPath(control.sourceRef.agentSlug)}>
        Edit agent {control.sourceRef.agentSlug} in Agent Studio
      </Link>
    );
  }
  if (workflowSlug) {
    return <Link to={studioWorkflowPath(workflowSlug)}>Edit workflow {workflowSlug} in Workflow Studio</Link>;
  }
  return <>Not editable here.</>;
}
