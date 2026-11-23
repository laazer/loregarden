import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { previewIsStarting, reviewApi } from "../api/reviewApi";

const POLL_MS = 2000;

export function previewKey(workspaceSlug: string, ref: string) {
  return ["review-preview", workspaceSlug, ref] as const;
}

/**
 * The running instance of a build, for opening and capturing its pages. Polls
 * only while an instance is starting, so a page left open does not ask the
 * server every two seconds for nothing.
 */
export function useReviewPreview(workspaceSlug: string, ref: string, enabled: boolean) {
  const qc = useQueryClient();
  const key = previewKey(workspaceSlug, ref);
  const status = useQuery({
    queryKey: key,
    queryFn: () => reviewApi.preview(workspaceSlug, ref),
    enabled,
    meta: { suppressErrorToast: true },
    refetchInterval: (query) => (previewIsStarting(query.state.data) ? POLL_MS : false),
  });
  const start = useMutation({
    meta: { errorTitle: `Start a preview of ${ref}` },
    mutationFn: () => reviewApi.startPreview(workspaceSlug, ref),
    onSuccess: (view) => qc.setQueryData(key, view),
  });
  return { status, start };
}
