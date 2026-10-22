import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";

import { api } from "../../api/client";
import type { AddSourceResult, SourceKind } from "../../api/referenceLibraryApi";
import { describeError, pushToast } from "../../state/toastStore";
import { useUiStore } from "../../state/uiStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { Select } from "../ui/Select";
import { Textarea } from "../ui/Textarea";

const KINDS: { value: SourceKind; label: string }[] = [
  { value: "repo", label: "Repository" },
  { value: "site", label: "Website or docs" },
  { value: "released_app", label: "Released app (no source)" },
];

function announce(result: AddSourceResult, workspace: string) {
  const { source } = result;
  if (source.state === "awaiting_approval") {
    pushToast({
      tone: "success",
      title: `Proposed ${source.name}`,
      message: "Approve its card in the inbox to queue it; nothing in it is read before then.",
    });
  } else if (result.scoped_pass) {
    pushToast({
      tone: "success",
      title: `${source.name} now serves ${workspace}`,
      message: `It was already surveyed, so pass ${result.scoped_pass} reads it again against your question.`,
    });
  } else {
    pushToast({
      tone: "success",
      title: `${source.name} now serves ${workspace}`,
      message: "Its waiting pass reads for this workspace too.",
    });
  }
}

/**
 * Propose a repository or site for the survey, for this workspace. A new one
 * waits for its admission card in the inbox; one already known gains the
 * workspace instead of being surveyed twice.
 */
export function ReferenceSourceForm({
  workspace,
  initialUrl = "",
  initialKind = "repo",
  onDone,
  onCancel,
}: {
  workspace: string;
  initialUrl?: string;
  initialKind?: SourceKind;
  onDone: (result: AddSourceResult) => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const [url, setUrl] = useState(initialUrl);
  const [kind, setKind] = useState<SourceKind>(initialKind);
  const [question, setQuestion] = useState("");
  const [why, setWhy] = useState("");
  const [failure, setFailure] = useState("");
  const ids = { url: useId(), kind: useId(), question: useId(), why: useId() };

  const add = useMutation({
    mutationFn: () =>
      api.addReferenceSource({
        workspace_slugs: [workspace],
        kind,
        url: url.trim(),
        question: question.trim(),
        queued_reason: why.trim(),
      }),
    onSuccess: (result) => {
      setFailure("");
      announce(result, workspace);
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
      void queryClient.invalidateQueries({ queryKey: ["reference-source", result.source.id] });
      if (result.source.state === "awaiting_approval") setInboxOpen(true);
      onDone(result);
    },
    onError: (error) => {
      const message = describeError(error, "the request failed");
      setFailure(message);
      pushToast({ tone: "error", title: "The reference was not added", message });
    },
  });

  const ready = url.trim() && question.trim() && why.trim();
  function submit(event: FormEvent) {
    event.preventDefault();
    if (ready && !add.isPending) add.mutate();
  }

  return (
    <form
      className="rl-add"
      aria-label="Add a reference"
      onSubmit={submit}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onCancel();
        }
      }}
    >
      <div className="rl-add-row">
        <div className="rl-field rl-field--grow">
          <label htmlFor={ids.url}>URL</label>
          <Input
            id={ids.url}
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://github.com/owner/repo"
            readOnly={Boolean(initialUrl)}
            autoFocus={!initialUrl}
            required
          />
        </div>
        <div className="rl-field">
          <label htmlFor={ids.kind}>Kind</label>
          <Select id={ids.kind} value={kind} onChange={(event) => setKind(event.target.value as SourceKind)}>
            {KINDS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <div className="rl-field">
        <label htmlFor={ids.question}>What should the survey answer from it?</label>
        <Textarea
          id={ids.question}
          rows={2}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="How do they retry a failed run without losing its state?"
          autoFocus={Boolean(initialUrl)}
          required
        />
      </div>
      <div className="rl-field">
        <label htmlFor={ids.why}>Why this one?</label>
        <Textarea
          id={ids.why}
          rows={2}
          value={why}
          onChange={(event) => setWhy(event.target.value)}
          placeholder="It ships a retry ledger we could compare with ours."
          required
        />
      </div>
      {failure ? (
        <p className="rl-warn" role="alert">
          Not added: {failure}
        </p>
      ) : null}
      <div className="rl-form-actions">
        <Button type="submit" variant="primary" compact disabled={!ready || add.isPending}>
          {add.isPending ? "Adding…" : "Propose for review"}
        </Button>
        <Button variant="plain" compact onClick={onCancel}>
          Cancel
        </Button>
        <span className="rl-muted">A new source waits for your approval in the inbox before anything is read.</span>
      </div>
    </form>
  );
}
