import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../api/client";
import type { LibraryDetail, LinkTargetsResult } from "../../api/referenceLibraryApi";
import { linkTargetsSummary } from "../../lib/referenceLibrary";
import { describeError, pushToast } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";
import { Textarea } from "../ui/Textarea";
import { ReferenceTargetsPicker } from "./ReferenceTargetsPicker";

/**
 * Link several workspaces and initiatives to a source at once, in a modal. A
 * workspace joins as enqueueing the URL from it would (a surveyed source gets
 * one pass for it); an initiative gains the source's open tickets, now and as
 * passes add more. Every name is checked before anything is written.
 */
export function ReferenceLinkButton({
  detail,
  onLinked,
}: {
  detail: LibraryDetail;
  onLinked?: (result: LinkTargetsResult) => void;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [initiatives, setInitiatives] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [why, setWhy] = useState("");
  const [failure, setFailure] = useState("");
  const ids = { title: useId(), question: useId(), why: useId() };
  const { source } = detail;

  function close() {
    setOpen(false);
    setWorkspaces([]);
    setInitiatives([]);
    setQuestion("");
    setWhy("");
    setFailure("");
  }

  const link = useMutation({
    mutationFn: () =>
      api.linkSourceTargets(source.id, {
        workspace_slugs: workspaces,
        initiative_ids: initiatives,
        question: question.trim(),
        queued_reason: why.trim(),
      }),
    onSuccess: (result) => {
      pushToast({ tone: "success", title: `Linked to ${source.name}`, message: linkTargetsSummary(source.name, result) });
      onLinked?.(result);
      close();
      void queryClient.invalidateQueries({ queryKey: ["reference-source", source.id] });
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
      void queryClient.invalidateQueries({ queryKey: ["initiatives"] });
    },
    onError: (error) => {
      const message = describeError(error, "the request failed");
      setFailure(message);
      pushToast({ tone: "error", title: "Nothing was linked", message });
    },
  });

  if (source.state === "refused") return null;
  const picked = workspaces.length + initiatives.length;
  return (
    <>
      <Button variant="secondary" compact aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(true)}>
        Link workspaces and initiatives…
      </Button>
      <ModalShell open={open} onDismiss={link.isPending ? undefined : close} labelledBy={ids.title} panelClassName="modal-panel-wide">
        <form
          aria-label={`Link to ${source.name}`}
          onSubmit={(event) => {
            event.preventDefault();
            if (picked > 0 && !link.isPending) link.mutate();
          }}
        >
          <div className="modal-header">
            <div>
              <h2 id={ids.title} className="modal-title">
                Link to {source.name}
              </h2>
              <p className="modal-subtitle">
                Tick any number of each. That is enough for its next review to read for them.
              </p>
            </div>
            <IconCloseButton disabled={link.isPending} onClick={close} />
          </div>
          <div className="modal-body rl-modal-body">
            <ReferenceTargetsPicker
              fixedWorkspaces={source.workspaces}
              workspaces={workspaces}
              onWorkspaces={setWorkspaces}
              fixedInitiatives={detail.initiatives.map((i) => i.id)}
              initiatives={initiatives}
              onInitiatives={setInitiatives}
            />
            {workspaces.length > 0 ? (
              <>
                <label htmlFor={ids.question}>What should it answer for them? (optional)</label>
                <Textarea
                  id={ids.question}
                  rows={2}
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                />
                <label htmlFor={ids.why}>Why there? (optional)</label>
                <Textarea id={ids.why} rows={2} value={why} onChange={(event) => setWhy(event.target.value)} />
              </>
            ) : null}
            {failure ? (
              <p className="rl-warn" role="alert">
                Nothing was linked: {failure}
              </p>
            ) : null}
          </div>
          <div className="modal-footer">
            <Button variant="secondary" disabled={link.isPending} onClick={close}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={picked === 0 || link.isPending}>
              {link.isPending ? "Linking…" : picked ? `Link ${picked}` : "Link"}
            </Button>
          </div>
        </form>
      </ModalShell>
    </>
  );
}
