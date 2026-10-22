import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";

import { api } from "../../api/client";
import type {
  AddSourceResult,
  BulkAddItem,
  SourceKind,
} from "../../api/referenceLibraryApi";
import { memoryPath } from "../../lib/appNavigation";
import { splitUrls } from "../../lib/referenceLibrary";
import { describeError, pushToast } from "../../state/toastStore";
import { useUiStore } from "../../state/uiStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { Select } from "../ui/Select";
import { ModalShell } from "../ui/ModalShell";
import { Textarea } from "../ui/Textarea";
import { IconCloseButton } from "../IconCloseButton";
import { ReferenceTargetsPicker } from "./ReferenceTargetsPicker";

/** The server takes this many URLs per bulk add (`MAX_BULK_URLS`). */
const MAX_BULK_URLS = 50;
const DETECT = "detect";

const KINDS: { value: SourceKind; label: string }[] = [
  { value: "repo", label: "Repository" },
  { value: "site", label: "Website or docs" },
  { value: "released_app", label: "Released app (no source)" },
];

const OUTCOME_TEXT: Record<BulkAddItem["outcome"], string> = {
  proposed: "proposed — waiting for approval in the inbox",
  attached: "already in the library; now serves this workspace too",
  rescoped: "already surveyed; a new pass reads it for this workspace",
  already: "already in the library for this workspace; nothing changed",
  failed: "not added",
};

function announce(result: AddSourceResult, workspace: string) {
  const { source } = result;
  if (source.state === "awaiting_approval") {
    pushToast({
      tone: "success",
      title: `Proposed ${source.name}`,
      message:
        "Approve its card in the inbox to queue it; nothing in it is read before then.",
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

function announceBulk(items: BulkAddItem[]) {
  const count = (outcome: BulkAddItem["outcome"]) =>
    items.filter((item) => item.outcome === outcome).length;
  const failed = count("failed");
  const known = count("already");
  const added = items.length - failed - known;
  const notes = [
    known ? `${known} already in the library` : "",
    failed
      ? `${failed} could not be added and ${failed === 1 ? "is" : "are"} left in the box, the reason beside each`
      : "",
  ].filter(Boolean);
  pushToast({
    tone: failed ? (added ? "warning" : "error") : "success",
    title: `Added ${added} of ${items.length} reference${items.length === 1 ? "" : "s"}`,
    message: notes.length
      ? `${notes.join("; ")}.`
      : "New ones wait for approval in the inbox before anything in them is read.",
  });
}

function BulkResults({ items }: { items: BulkAddItem[] }) {
  const { search } = useLocation();
  return (
    <ul className="rl-bulk-results" aria-label="What each URL did">
      {items.map((item) => (
        <li
          key={item.url}
          className={item.outcome === "failed" ? "rl-bulk-failed" : undefined}
        >
          {item.source ? (
            <Link
              className="rl-bulk-url"
              to={{ pathname: memoryPath("library", item.source.id), search }}
            >
              {item.url}
            </Link>
          ) : (
            <span className="rl-bulk-url">{item.url}</span>
          )}{" "}
          {OUTCOME_TEXT[item.outcome]}
          {item.detail ? `: ${item.detail}` : ""}
        </li>
      ))}
    </ul>
  );
}

/**
 * Propose repositories or sites for the survey, for this workspace: one, or
 * several pasted together (separated by commas, tabs or new lines) sharing one
 * question and reason. A new one waits for its admission card in the inbox; one
 * already known gains the workspace instead of being surveyed twice.
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
  const [several, setSeveral] = useState(false);
  const [url, setUrl] = useState(initialUrl);
  const [urlsText, setUrlsText] = useState("");
  const [kind, setKind] = useState<SourceKind>(initialKind);
  const [bulkKind, setBulkKind] = useState<SourceKind | typeof DETECT>(DETECT);
  const [question, setQuestion] = useState("");
  const [why, setWhy] = useState("");
  const [failure, setFailure] = useState("");
  const [results, setResults] = useState<BulkAddItem[]>([]);
  const [alsoFor, setAlsoFor] = useState<string[]>([]);
  const [initiativeIds, setInitiativeIds] = useState<string[]>([]);
  const workspaceSlugs = [
    workspace,
    ...alsoFor.filter((slug) => slug !== workspace),
  ];
  const ids = {
    title: useId(),
    url: useId(),
    kind: useId(),
    question: useId(),
    why: useId(),
    hint: useId(),
  };
  const urls = splitUrls(urlsText);

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
    void queryClient.invalidateQueries({ queryKey: ["reference-source"] });
  }

  function failed(error: unknown) {
    const message = describeError(error, "the request failed");
    setFailure(message);
    pushToast({ tone: "error", title: "The reference was not added", message });
  }

  const add = useMutation({
    mutationFn: () =>
      api.addReferenceSource({
        workspace_slugs: workspaceSlugs,
        kind,
        url: url.trim(),
        question: question.trim(),
        queued_reason: why.trim(),
        initiative_ids: initiativeIds,
      }),
    onSuccess: (result) => {
      setFailure("");
      announce(result, workspace);
      refresh();
      if (result.source.state === "awaiting_approval") setInboxOpen(true);
      onDone(result);
    },
    onError: failed,
  });

  const addMany = useMutation({
    mutationFn: () =>
      api.addReferenceSources({
        workspace_slugs: workspaceSlugs,
        urls,
        kind: bulkKind === DETECT ? null : bulkKind,
        question: question.trim(),
        queued_reason: why.trim(),
        initiative_ids: initiativeIds,
      }),
    onSuccess: (items) => {
      setFailure("");
      setResults(items);
      announceBulk(items);
      refresh();
      // What failed stays in the box to fix and resend; what was added leaves it.
      setUrlsText(
        items
          .filter((item) => item.outcome === "failed")
          .map((item) => item.url)
          .join("\n"),
      );
      if (items.some((item) => item.outcome === "proposed")) setInboxOpen(true);
    },
    onError: failed,
  });

  const pending = add.isPending || addMany.isPending;
  const tooMany = several && urls.length > MAX_BULK_URLS;
  const ready = several ? urls.length > 0 && !tooMany : Boolean(url.trim());
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || pending) return;
    if (several) addMany.mutate();
    else add.mutate();
  }

  let submitLabel = "Propose for review";
  if (pending) submitLabel = "Adding…";
  else if (several) submitLabel = `Propose ${urls.length} for review`;

  return (
    <ModalShell
      open
      onDismiss={pending ? undefined : onCancel}
      labelledBy={ids.title}
      panelClassName={several ? "modal-panel-wide" : undefined}
    >
      <form aria-label="Add a reference" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2 id={ids.title} className="modal-title">
              {initialUrl ? "Propose for review" : "Add a reference"}
            </h2>
            <p className="modal-subtitle">
              {several
                ? "Each new one gets its own card in the inbox; nothing is read before it is approved."
                : "A new source waits for your approval in the inbox before anything is read."}
            </p>
          </div>
          <IconCloseButton disabled={pending} onClick={onCancel} />
        </div>
        <div className="modal-body rl-modal-body">
          {initialUrl ? null : (
            <div className="rl-add-mode" role="group" aria-label="How many">
              <Button
                variant="plain"
                compact
                className="rl-view"
                aria-pressed={!several}
                onClick={() => setSeveral(false)}
              >
                One
              </Button>
              <Button
                variant="plain"
                compact
                className="rl-view"
                aria-pressed={several}
                onClick={() => setSeveral(true)}
              >
                Several
              </Button>
            </div>
          )}
          {several ? (
            <div className="rl-add-row">
              <div className="rl-field rl-field--grow">
                <label htmlFor={ids.url}>URLs</label>
                <Textarea
                  id={ids.url}
                  rows={4}
                  value={urlsText}
                  onChange={(event) => setUrlsText(event.target.value)}
                  placeholder={
                    "https://github.com/owner/one, https://github.com/owner/two\nhttps://docs.example.dev"
                  }
                  aria-describedby={ids.hint}
                  autoFocus
                />
                <span
                  id={ids.hint}
                  className={tooMany ? "rl-warn" : "rl-muted"}
                  aria-live="polite"
                >
                  {tooMany
                    ? `${urls.length} URLs; at most ${MAX_BULK_URLS} at a time.`
                    : `Separate them with commas, tabs or new lines. ${urls.length} URL${urls.length === 1 ? "" : "s"}.`}
                </span>
              </div>
              <div className="rl-field">
                <label htmlFor={ids.kind}>Kind</label>
                <Select
                  id={ids.kind}
                  value={bulkKind}
                  onChange={(event) =>
                    setBulkKind(
                      event.target.value as SourceKind | typeof DETECT,
                    )
                  }
                >
                  <option value={DETECT}>Detect from each URL</option>
                  {KINDS.map((option) => (
                    <option key={option.value} value={option.value}>
                      All: {option.label}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          ) : (
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
                <Select
                  id={ids.kind}
                  value={kind}
                  onChange={(event) =>
                    setKind(event.target.value as SourceKind)
                  }
                >
                  {KINDS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          )}
          <ReferenceTargetsPicker
            fixedWorkspaces={[workspace]}
            workspaces={alsoFor}
            onWorkspaces={setAlsoFor}
            initiatives={initiativeIds}
            onInitiatives={setInitiativeIds}
          />
          <p className="rl-muted rl-optional-note">
            That is enough to review {several ? "them" : "it"}: the survey reads
            for those workspaces and initiatives. Add specifics below if you
            have them.
          </p>
          <div className="rl-field">
            <label htmlFor={ids.question}>
              What should the survey answer from {several ? "them" : "it"}?
              (optional)
            </label>
            <Textarea
              id={ids.question}
              rows={2}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="How do they retry a failed run without losing its state?"
              autoFocus={Boolean(initialUrl)}
            />
          </div>
          <div className="rl-field">
            <label htmlFor={ids.why}>
              {several ? "Why these?" : "Why this one?"} (optional)
            </label>
            <Textarea
              id={ids.why}
              rows={2}
              value={why}
              onChange={(event) => setWhy(event.target.value)}
              placeholder="It ships a retry ledger we could compare with ours."
            />
          </div>
          {failure ? (
            <p className="rl-warn" role="alert">
              Not added: {failure}
            </p>
          ) : null}
          {several && results.length > 0 ? (
            <BulkResults items={results} />
          ) : null}
        </div>
        <div className="modal-footer">
          <Button variant="secondary" disabled={pending} onClick={onCancel}>
            {several && results.length > 0 ? "Done" : "Cancel"}
          </Button>
          <Button type="submit" variant="primary" disabled={!ready || pending}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}
