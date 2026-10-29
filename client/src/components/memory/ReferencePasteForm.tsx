import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState, type FormEvent } from "react";

import { api } from "../../api/client";
import type { BulkAddItem } from "../../api/referenceLibraryApi";
import { entriesFromPaste, type PastedEntry } from "../../lib/referenceLibrary";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";
import { Textarea } from "../ui/Textarea";
import { IconCloseButton } from "../IconCloseButton";
import { ReferenceTargetsPicker } from "./ReferenceTargetsPicker";
import { MAX_BULK_URLS, announceBulk } from "./bulkAdd";
import { BulkResults } from "./ReferenceSourceForm";

type Edit = Partial<Pick<PastedEntry, "name" | "why">>;

/**
 * Paste a list or an article of links and queue them for review: each link found
 * becomes a row with the name and description the text gave it, which become that
 * source's name and its reason for being read. Rows can be corrected or dropped
 * before anything is added.
 */
export function ReferencePasteForm({ workspace, onClose }: { workspace: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  // Corrections and drops are keyed by URL, so editing the pasted text keeps them.
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [dropped, setDropped] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<BulkAddItem[]>([]);
  const [failure, setFailure] = useState("");
  const [alsoFor, setAlsoFor] = useState<string[]>([]);
  const [initiativeIds, setInitiativeIds] = useState<string[]>([]);
  const ids = { title: useId(), paste: useId(), hint: useId() };

  const found = useMemo(() => entriesFromPaste(text), [text]);
  const entries = useMemo(
    () => found.filter((entry) => !dropped.has(entry.url)).map((entry) => ({ ...entry, ...edits[entry.url] })),
    [found, dropped, edits],
  );
  const tooMany = entries.length > MAX_BULK_URLS;

  function edit(url: string, change: Edit) {
    setEdits((current) => ({ ...current, [url]: { ...current[url], ...change } }));
  }

  function drop(urls: string[]) {
    setDropped((current) => new Set([...current, ...urls]));
  }

  const add = useMutation({
    mutationFn: () =>
      api.addReferenceSources({
        workspace_slugs: [workspace, ...alsoFor.filter((slug) => slug !== workspace)],
        urls: entries.map((entry) => entry.url),
        kind: null,
        question: "",
        queued_reason: "",
        initiative_ids: initiativeIds,
        entries: entries.map((entry) => ({ url: entry.url, name: entry.name.trim(), queued_reason: entry.why.trim() })),
      }),
    onSuccess: (items) => {
      setFailure("");
      setResults(items);
      announceBulk(items);
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
      // What was added leaves the list; what failed stays to fix or drop.
      drop(items.filter((item) => item.outcome !== "failed").map((item) => item.url));
    },
    onError: (error) => {
      const message = describeError(error, "the request failed");
      setFailure(message);
      pushToast({ tone: "error", title: "The references were not added", message });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (entries.length === 0 || tooMany || add.isPending) return;
    add.mutate();
  }

  let hint = "Paste a list of links, a markdown page or an article. Each link becomes a row below.";
  if (tooMany) hint = `${entries.length} links; at most ${MAX_BULK_URLS} at a time. Drop some below.`;
  else if (entries.length > 0) hint = `Found ${entries.length} link${entries.length === 1 ? "" : "s"}. Check the names and reasons, then queue them.`;
  else if (found.length > 0) hint = "Every link in this text has been added or dropped. Paste more to add more.";
  else if (text.trim()) hint = "No http(s) link in what you pasted.";

  return (
    <ModalShell open onDismiss={add.isPending ? undefined : onClose} labelledBy={ids.title} panelClassName="modal-panel-wide">
      <form aria-label="Paste a list of references" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2 id={ids.title} className="modal-title">Paste a list of references</h2>
            <p className="modal-subtitle">
              Each link joins the review queue with what the text said about it; each licence is checked before anything in it is read.
            </p>
          </div>
          <IconCloseButton disabled={add.isPending} onClick={onClose} />
        </div>
        <div className="modal-body rl-modal-body">
          <div className="rl-field">
            <label htmlFor={ids.paste}>Text with links</label>
            <Textarea
              id={ids.paste}
              rows={6}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder={"1. Twenty – The open-source CRM built for AI agents\nGitHub: https://github.com/twentyhq/twenty"}
              aria-describedby={ids.hint}
              autoFocus
            />
            <span id={ids.hint} className={tooMany ? "rl-warn" : "rl-muted"} aria-live="polite">
              {hint}
            </span>
          </div>
          {entries.length > 0 ? (
            <table className="rl-paste-table" aria-label="Links to queue">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Link</th>
                  <th scope="col">Why read it</th>
                  <th scope="col">
                    <span className="rl-sr-only">Drop</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.url}>
                    <td data-label="Name">
                      <Input
                        value={entry.name}
                        onChange={(event) => edit(entry.url, { name: event.target.value })}
                        placeholder="From the URL"
                        aria-label={`Name for ${entry.url}`}
                      />
                    </td>
                    <td data-label="Link" className="rl-bulk-url">
                      {entry.url}
                    </td>
                    <td data-label="Why read it">
                      <Textarea
                        rows={3}
                        value={entry.why}
                        onChange={(event) => edit(entry.url, { why: event.target.value })}
                        placeholder="No reason given"
                        aria-label={`Why read ${entry.url}`}
                      />
                    </td>
                    <td>
                      <Button variant="secondary" compact disabled={add.isPending} onClick={() => drop([entry.url])} aria-label={`Drop ${entry.url}`}>
                        Drop
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          <ReferenceTargetsPicker
            fixedWorkspaces={[workspace]}
            workspaces={alsoFor}
            onWorkspaces={setAlsoFor}
            initiatives={initiativeIds}
            onInitiatives={setInitiativeIds}
          />
          {failure ? (
            <p className="rl-warn" role="alert">
              Not added: {failure}
            </p>
          ) : null}
          {results.length > 0 ? <BulkResults items={results} /> : null}
        </div>
        <div className="modal-footer">
          <Button variant="secondary" disabled={add.isPending} onClick={onClose}>
            {results.length > 0 ? "Done" : "Cancel"}
          </Button>
          <Button type="submit" variant="primary" disabled={entries.length === 0 || tooMany || add.isPending}>
            {add.isPending ? "Adding…" : entries.length ? `Queue ${entries.length} for review` : "Queue for review"}
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}
