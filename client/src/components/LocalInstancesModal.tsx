import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { localInstancesApi } from "../api/localInstancesApi";
import type { LocalInstance, LocalInstanceLaunch } from "../api/localInstancesTypes";
import { useDialogDismiss } from "../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../hooks/useDialogFocusTrap";
import { pushToast, toastActionFailed } from "../state/toastStore";
import { IconCloseButton } from "./IconCloseButton";
import { LocalInstanceLaunchForm } from "./LocalInstanceLaunchForm";
import { LocalInstanceRow } from "./LocalInstanceRow";

import "./LocalInstancesModal.css";

/** Faster while anything is starting: listing is what advances it to ready. */
const BUSY_POLL_MS = 1500;
const IDLE_POLL_MS = 5000;
const INSTANCES_KEY = ["local-instances"] as const;

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
  const queryClient = useQueryClient();
  const [stopping, setStopping] = useState<ReadonlySet<string>>(new Set());

  const instances = useQuery({
    queryKey: INSTANCES_KEY,
    queryFn: localInstancesApi.list,
    enabled: open,
    refetchInterval: (query) =>
      query.state.data?.instances.some((i) => i.state === "starting") ? BUSY_POLL_MS : IDLE_POLL_MS,
  });
  const instanceIds = (instances.data?.instances ?? []).map((i) => i.id).join(",");
  const templates = useQuery({
    // A client's choice of server lists the running branch servers, so the
    // templates follow the set of instances — not every poll of it.
    queryKey: [...INSTANCES_KEY, "templates", instanceIds],
    queryFn: localInstancesApi.templates,
    enabled: open,
    placeholderData: (previous) => previous,
  });

  const launch = useMutation({
    mutationFn: (body: LocalInstanceLaunch) => localInstancesApi.launch(body),
    onSuccess: (created) => {
      pushToast({ tone: "success", title: `Launching ${created.name}`, message: created.url });
      void queryClient.invalidateQueries({ queryKey: INSTANCES_KEY });
    },
    onError: (error) => toastActionFailed("Launch instance", error),
  });

  const stop = (instance: LocalInstance) => {
    setStopping((prev) => new Set(prev).add(instance.id));
    localInstancesApi
      .stop(instance.id)
      .then(() => queryClient.invalidateQueries({ queryKey: INSTANCES_KEY }))
      .catch((error: unknown) => toastActionFailed(`Stop ${instance.name}`, error))
      .finally(() =>
        setStopping((prev) => {
          const next = new Set(prev);
          next.delete(instance.id);
          return next;
        }),
      );
  };

  if (!open) return null;

  const listing = instances.data;
  const names = new Map((listing?.instances ?? []).map((i) => [i.id, i.name]));

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
                  targetName={
                    instance.target_instance_id
                      ? (names.get(instance.target_instance_id) ?? `${instance.target_instance_id} (gone)`)
                      : undefined
                  }
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
