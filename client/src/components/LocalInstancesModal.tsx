import { useLocalInstances } from "../hooks/useLocalInstances";
import { navigateToPage } from "../lib/useAppNavigation";
import { IconCloseButton } from "./IconCloseButton";
import { LocalInstanceLaunchForm } from "./LocalInstanceLaunchForm";
import { LocalInstanceRow } from "./LocalInstanceRow";

import "./LocalInstancesModal.css";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";

interface LocalInstancesModalProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Branch servers and clients on their own ports, beside the main server.
 *
 * A UI-only change: launch a *client* from its worktree against main. A server
 * change: launch a *server* from its worktree (sandboxed, on a snapshot of
 * main's database), then a client pointed at it.
 */
export function LocalInstancesModal({ open, onClose }: LocalInstancesModalProps) {
  const { instances, templates, launch, stop, stopping, targetName } = useLocalInstances(open);

  if (!open) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="local-instances-title">{null}</ModalShell>;
  }

  const listing = instances.data;

  return (
    <ModalShell open onDismiss={onClose} labelledBy="local-instances-title" panelClassName="local-instances-panel">
      <div className="modal-header">
        <div>
          <div className="state-label">Development</div>
          <h2 id="local-instances-title" className="modal-title">
            Local instances
          </h2>
          <p className="modal-subtitle">
            Run a worktree's client against main, or its own sandboxed server on a free port.
          </p>
        </div>
        <IconCloseButton onClick={onClose} />
      </div>

      <div className="modal-body local-instances-body" aria-busy={instances.isPending}>
        {instances.error && (
          <p className="local-instances-error" role="alert">
            Could not load instances: {instances.error.message}
            {listing ? " — showing the last list that loaded." : ""}
          </p>
        )}
        {listing?.unreadable.map((bad) => (
          <p key={bad.path} className="local-instances-error">
            Unreadable registry record {bad.path}: {bad.error}
          </p>
        ))}

        {instances.isPending && !instances.error ? (
          <div className="local-instances-list" aria-label="Loading instances">
            <div className="local-instances-skeleton" />
            <div className="local-instances-skeleton" />
          </div>
        ) : listing && listing.instances.length === 0 ? (
          <p className="modal-hint">
            Nothing is registered — not even main, which appears here once started with{" "}
            <code>task server</code>. Launch a client below to try a UI change, or a server for a
            backend change.
          </p>
        ) : listing ? (
          <ul className="local-instances-list">
            {listing.instances.map((instance) => (
              <LocalInstanceRow
                key={instance.id}
                instance={instance}
                targetName={targetName(instance)}
                stopping={stopping.has(instance.id)}
                onStop={stop}
              />
            ))}
          </ul>
        ) : null}

        {templates.error && (
          <p className="local-instances-error" role="alert">
            Could not load launch templates: {templates.error.message}
          </p>
        )}
        {templates.data && (
          <LocalInstanceLaunchForm
            templates={templates.data}
            launching={launch.isPending}
            onLaunch={(body) => launch.mutate(body)}
          />
        )}
      </div>

      <div className="modal-footer">
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            onClose();
            navigateToPage("workspaces");
          }}
        >
          All workspaces and templates
        </Button>
        <Button
          variant="secondary"
          disabled={instances.isFetching}
          onClick={() => void instances.refetch()}
        >
          {instances.isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      </div>
    </ModalShell>
  );
}
