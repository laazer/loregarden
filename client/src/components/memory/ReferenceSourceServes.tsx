import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../api/client";
import type { AddSourceResult, LibraryDetail } from "../../api/referenceLibraryApi";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Textarea } from "../ui/Textarea";
import { ReferenceTargetsPicker } from "./ReferenceTargetsPicker";

function served(result: AddSourceResult): string {
  const who = result.attached.join(", ");
  if (result.scoped_pass) return `Pass ${result.scoped_pass} reads ${result.source.name} again for ${who}.`;
  if (result.source.state === "awaiting_approval") return `Once approved, its first pass reads for ${who} too.`;
  return `Its waiting pass reads for ${who} too.`;
}

/**
 * Make this source answer more workspaces. One reading serves them all: a
 * surveyed source gets one pass on the new question, a waiting one reads for
 * them too. Agents do the same by enqueueing the URL from the other workspace.
 */
export function ReferenceSourceServes({ detail }: { detail: LibraryDetail }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [why, setWhy] = useState("");
  const ids = { question: useId(), why: useId() };
  const { source } = detail;
  const add = useMutation({
    mutationFn: () =>
      api.serveSourceInWorkspaces(source.id, {
        workspace_slugs: workspaces,
        question: question.trim(),
        queued_reason: why.trim(),
      }),
    onSuccess: (result) => {
      pushToast({ tone: "success", title: `${source.name} now serves ${result.attached.join(", ")}`, message: served(result) });
      setOpen(false);
      setWorkspaces([]);
      void queryClient.invalidateQueries({ queryKey: ["reference-source", source.id] });
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Could not add the workspace", message: describeError(error) }),
  });

  if (source.state === "refused") return null;
  if (!open) {
    return (
      <Button variant="plain" compact className="rl-more" aria-expanded={false} onClick={() => setOpen(true)}>
        Add a workspace…
      </Button>
    );
  }
  const ready = workspaces.length > 0;
  return (
    <form
      className="rl-review-again"
      aria-label={`Add a workspace to ${source.name}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (ready && !add.isPending) add.mutate();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <ReferenceTargetsPicker
        fixedWorkspaces={source.workspaces}
        workspaces={workspaces}
        onWorkspaces={setWorkspaces}
        initiatives={[]}
        onInitiatives={() => undefined}
        showInitiatives={false}
      />
      <label htmlFor={ids.question}>What should it answer for them? (optional)</label>
      <Textarea id={ids.question} rows={2} value={question} onChange={(event) => setQuestion(event.target.value)} />
      <label htmlFor={ids.why}>Why there? (optional)</label>
      <Textarea id={ids.why} rows={2} value={why} onChange={(event) => setWhy(event.target.value)} />
      <div className="rl-form-actions">
        <Button type="submit" variant="primary" compact disabled={!ready || add.isPending}>
          {add.isPending ? "Adding…" : "Add"}
        </Button>
        <Button variant="plain" compact aria-expanded onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
