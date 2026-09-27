import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { localInstancesApi } from "../api/localInstancesApi";
import type { LocalInstance, LocalInstanceLaunch } from "../api/localInstancesTypes";
import { pushToast, toastActionFailed } from "../state/toastStore";

/** Faster while anything is starting: listing is what advances it to ready. */
const BUSY_POLL_MS = 1500;
const IDLE_POLL_MS = 5000;
export const INSTANCES_KEY = ["local-instances"] as const;
/** Under INSTANCES_KEY, so anything that refreshes instances refreshes this too. */
export const WORKSPACE_TEMPLATES_KEY = [...INSTANCES_KEY, "workspace-templates"] as const;

/**
 * Instances, launchable templates, and launch/stop — shared by the topbar
 * modal and the Instances page so both see and do the same thing.
 *
 * `enabled` is false while the surface is closed, so a hidden modal does not
 * poll. Stops in flight are tracked per id: a second click on the same Stop
 * does nothing while the first is out.
 */
export function useLocalInstances(enabled: boolean) {
  const queryClient = useQueryClient();
  const [stopping, setStopping] = useState<ReadonlySet<string>>(new Set());

  const instances = useQuery({
    queryKey: INSTANCES_KEY,
    queryFn: localInstancesApi.list,
    enabled,
    refetchInterval: (query) =>
      query.state.data?.instances.some((i) => i.state === "starting") ? BUSY_POLL_MS : IDLE_POLL_MS,
  });
  const instanceIds = (instances.data?.instances ?? []).map((i) => i.id).join(",");
  const templates = useQuery({
    // A client's choice of server lists the running servers, so the templates
    // follow the set of instances — not every poll of it.
    queryKey: [...INSTANCES_KEY, "templates", instanceIds],
    queryFn: localInstancesApi.templates,
    enabled,
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
    if (stopping.has(instance.id)) return;
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

  const names = new Map((instances.data?.instances ?? []).map((i) => [i.id, i.name]));
  const targetName = (instance: LocalInstance): string | undefined =>
    instance.target_instance_id
      ? (names.get(instance.target_instance_id) ?? `${instance.target_instance_id} (gone)`)
      : undefined;

  return { instances, templates, launch, stop, stopping, targetName };
}
