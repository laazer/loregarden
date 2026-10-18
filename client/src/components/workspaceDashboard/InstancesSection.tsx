import { Link } from "react-router-dom";

import type { LocalInstanceState } from "../../api/localInstancesTypes";
import { useLocalInstances } from "../../hooks/useLocalInstances";
import { workspacesPath } from "../../lib/appNavigation";
import { formatRelativeAge } from "../../lib/timestamps";
import { Button } from "../ui/Button";
import { DashboardSection } from "./DashboardSection";

const STATE_LABELS: Record<LocalInstanceState, string> = {
  starting: "Starting",
  ready: "Ready",
  stalled: "Stalled",
  exited: "Exited",
};

interface InstancesSectionProps {
  workspaceSlug: string;
  now: Date;
}

/** "What is running from this repo, and where do I open it?" Stop is the one action. */
export function InstancesSection({ workspaceSlug, now }: InstancesSectionProps) {
  const { instances, stop, stopping } = useLocalInstances(true);
  const mine = (instances.data?.instances ?? []).filter((instance) => instance.project === workspaceSlug);

  return (
    <DashboardSection
      id="wsd-instances"
      title="Running instances"
      count={instances.data ? String(mine.length) : undefined}
      queries={[instances]}
      errorTitle="Could not list running instances"
      empty={mine.length === 0}
      emptyMessage={
        <>
          Nothing is running from {workspaceSlug}.{" "}
          <Link to={workspacesPath("instances")}>Launch one from Instances</Link>.
        </>
      }
      skeletonRows={2}
    >
      <ul className="wsd-rows">
        {mine.map((instance) => (
          <li key={instance.id} className="wsd-row">
            {instance.url ? (
              <a href={instance.url} target="_blank" rel="noreferrer" className="wsd-row-link">
                <span className="wsd-row-title">{instance.name}</span>
                <span className="wsd-id">{instance.kind}</span>
              </a>
            ) : (
              <span className="wsd-row-link">
                <span className="wsd-row-title">{instance.name}</span>
                <span className="wsd-id">{instance.kind}</span>
              </span>
            )}
            <span className="wsd-row-aside">
              <span className={`wsd-tag wsd-tag--instance-${instance.state}`}>{STATE_LABELS[instance.state]}</span>
              <span className="wsd-row-where">{formatRelativeAge(instance.started_at, now) || "just now"}</span>
              {instance.last_error ? <span className="wsd-danger">{instance.last_error}</span> : null}
              {instance.managed && instance.state !== "exited" ? (
                <Button
                  variant="secondary"
                  compact
                  disabled={stopping.has(instance.id)}
                  onClick={() => stop(instance)}
                  aria-label={`Stop ${instance.name}`}
                >
                  {stopping.has(instance.id) ? "Stopping…" : "Stop"}
                </Button>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </DashboardSection>
  );
}
