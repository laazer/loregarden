import { useMutation, useQueryClient } from "@tanstack/react-query";

import { reviewApi, type ReviewBuild } from "../api/reviewApi";

export const REVIEW_QUEUE_KEY = ["review-queue"] as const;

export function launchStatusKey(workspaceSlug: string) {
  return ["review-launch", workspaceSlug] as const;
}

/**
 * Launch (or relaunch) a build's review checkout, optionally at one scene.
 * Shared by the build's Launch button and each ticket's Open scene button so
 * both refresh the same status.
 */
export function useLaunchBuild(build: ReviewBuild, errorTitle: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { errorTitle },
    mutationFn: (scene: string) => reviewApi.launch(build.workspace_slug, { ref: build.ref, scene }),
    onSettled: () => qc.invalidateQueries({ queryKey: launchStatusKey(build.workspace_slug) }),
  });
}
