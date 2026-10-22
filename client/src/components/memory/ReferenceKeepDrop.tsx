import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { LibraryRow } from "../../api/referenceLibraryApi";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";

/**
 * Decide an agent's proposal in one click: Keep queues it (its card approved),
 * Drop refuses it (its card rejected). A dropped source can still be reviewed
 * again later, with a reason.
 */
export function KeepOrDrop({ source }: { source: Pick<LibraryRow, "id" | "name"> }) {
  const queryClient = useQueryClient();
  function settled() {
    void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
    void queryClient.invalidateQueries({ queryKey: ["reference-source", source.id] });
  }
  const keep = useMutation({
    mutationFn: () => api.keepSource(source.id),
    onSuccess: () => {
      pushToast({ tone: "success", title: `Kept ${source.name}`, message: "It joins the review queue." });
      settled();
    },
    onError: (error) => pushToast({ tone: "error", title: `Could not keep ${source.name}`, message: describeError(error) }),
  });
  const drop = useMutation({
    mutationFn: () => api.dropSource(source.id, ""),
    onSuccess: () => {
      pushToast({ tone: "success", title: `Dropped ${source.name}`, message: "It moves to Refused; review it again there if you change your mind." });
      settled();
    },
    onError: (error) => pushToast({ tone: "error", title: `Could not drop ${source.name}`, message: describeError(error) }),
  });
  const busy = keep.isPending || drop.isPending;
  return (
    <span className="rl-keep-drop" role="group" aria-label={`Keep or drop ${source.name}`}>
      <Button variant="primary" compact disabled={busy} onClick={() => keep.mutate()}>
        {keep.isPending ? "Keeping…" : "Keep"}
      </Button>
      <Button variant="secondary" compact disabled={busy} onClick={() => drop.mutate()}>
        {drop.isPending ? "Dropping…" : "Drop"}
      </Button>
    </span>
  );
}
