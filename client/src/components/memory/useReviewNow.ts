import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { ReviewNowRequest, ReviewNowResult } from "../../api/referenceLibraryApi";
import { describeError, pushToast } from "../../state/toastStore";

function alsoText(result: ReviewNowResult): string {
  const parts = [];
  if (result.workspaces_added.length) parts.push(`it now reads for ${result.workspaces_added.join(", ")} too`);
  if (result.initiatives_linked.length) parts.push(`${result.initiatives_linked.join(", ")} now follow it`);
  return parts.length ? ` ${parts.join("; ")}.` : "";
}

function report(name: string, result: ReviewNowResult) {
  if (result.started) {
    pushToast({
      tone: "success",
      title: `Reviewing ${name}`,
      message: `Pass ${result.ordinal} is queued to run${result.carrier ? ` as ${result.carrier}` : ""}.${alsoText(result)}`,
    });
  } else if (result.refused) {
    pushToast({ tone: "warning", title: `${name} was not reviewed`, message: result.refused });
  } else {
    pushToast({
      tone: "warning",
      title: `${name} is first in line but has not started`,
      message: `Pass ${result.ordinal} is waiting: ${result.waiting.replace(/`/g, "")}.${alsoText(result)}`,
    });
  }
}

export const NOTHING_MORE: ReviewNowRequest = { question: "", workspace_slugs: [], initiative_ids: [] };

export function useReviewNow(sourceId: string, name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ReviewNowRequest) => api.reviewSourceNow(sourceId, body),
    onSuccess: (result) => {
      report(name, result);
      void queryClient.invalidateQueries({ queryKey: ["reference-source", sourceId] });
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
      // A pass that clones and dispatches closes the source's exception cards.
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: `Could not review ${name} now`, message: describeError(error) }),
  });
}
