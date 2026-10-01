import { useDialogDismiss } from "../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../hooks/useDialogFocusTrap";
import { useLocalInstances } from "../hooks/useLocalInstances";
import { navigateToPage } from "../lib/useAppNavigation";
import { IconCloseButton } from "./IconCloseButton";
import { LocalInstanceLaunchForm } from "./LocalInstanceLaunchForm";
import { LocalInstanceRow } from "./LocalInstanceRow";

import "./LocalInstancesModal.css";

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
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  useDialogDismiss(open ? onClose : null);
  const { instances, templates, launch, stop, stopping, targetName } = useLocalInstances(open);

  if (!open) return null;

  const listing = instances.data;

  return (
    <>
      <div className="modal-overlay" onClick={onClose} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel local-instances-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="local-instances-title"
      >
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
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              onClose();
              navigateToPage("workspaces");
            }}
          >
            All workspaces and templates
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={instances.isFetching}
            onClick={() => void instances.refetch()}
          >
            {instances.isFetching ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>
    </>
  );
}
