import { useMutation, useQueryClient } from "@tanstack/react-query";

import { reviewApi, type ReviewBuild } from "../api/reviewApi";

export const REVIEW_QUEUE_KEY = ["review-queue"] as const;

export function launchStatusKey(workspaceSlug: string) {
  return ["review-launch", workspaceSlug] as const;
}

/** One launch: a named target, or a scene in whichever target opens scenes. */
export interface LaunchChoice {
  target?: string;
  scene?: string;
}

/**
 * Launch (or relaunch) a build's review checkout, optionally at one scene.
 * Shared by the Launch buttons and each ticket's Open scene button so both
 * refresh the same status.
 */
export function useLaunchBuild(build: ReviewBuild, errorTitle: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { errorTitle },
    mutationFn: (choice: LaunchChoice) =>
      reviewApi.launch(build.workspace_slug, { ref: build.ref, ...choice }),
    onSettled: () => qc.invalidateQueries({ queryKey: launchStatusKey(build.workspace_slug) }),
  });
}
