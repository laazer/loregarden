import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";
import { formatBytes } from "../../lib/ticketOutputs";
import { useReaderStore, type ReaderPartial } from "../../state/readerStore";
import { describeError, pushToast } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { StructuredContent } from "./StructuredContent";
import "./Reader.css";

function sourceText(content: unknown): string {
  return typeof content === "string" ? content : JSON.stringify(content, null, 2);
}

/**
 * What the operator is looking at while the rest of a shortened document loads.
 *
 * `shownBytes` is optional on the wire — an older server, or a cached response,
 * can mark a row shortened without measuring it — and "Showing the first
 * undefined" is worse than not naming the number at all.
 */
function fillLine(partial: ReaderPartial): string {
  const rest = `loading all ${formatBytes(partial.totalBytes)}…`;
  return typeof partial.shownBytes === "number"
    ? `Showing the first ${formatBytes(partial.shownBytes)} — ${rest}`
    : `Showing the beginning of this output — ${rest}`;
}

/** What `Copy` would actually put on the clipboard, said in its own name. */
function copyName(partial: ReaderPartial): string {
  return typeof partial.shownBytes === "number"
    ? `Copy first ${formatBytes(partial.shownBytes)}`
    : "Copy the part shown";
}

/**
 * The reading view `openReader` opens: a wide dialog, body text at reading
 * size on a ~72ch measure, with the raw source one toggle away for when the
 * formatting is the thing in question. Mounted once, in `AppLayout`.
 *
 * A document can arrive holding only the head of its content — the artifact
 * feed bounds a row's content so a failing suite's log cannot be served to a
 * 2 s poll in full. Such a document carries a `partial`, and the rest is
 * fetched here and landed *into* the open document. There is no loading state
 * to design: the prefix is already on screen, which is where a reader starts,
 * so the body is never replaced and one status line says what is missing.
 */
export function ReaderHost() {
  const document = useReaderStore((state) => state.document);
  const close = useReaderStore((state) => state.close);
  const completeDocument = useReaderStore((state) => state.completeDocument);
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);

  useDialogDismiss(document ? close : null);

  const partial = document?.partial;
  const artifactId = partial?.artifactId;
  // Keyed on the artifact, not on the open document: a second click on the same
  // row is served from the cache rather than issuing a second request, and
  // `Try again` is this query's refetch. An artifact's stored content does not
  // change, so nothing here goes stale.
  const rest = useQuery({
    queryKey: ["artifact", artifactId],
    queryFn: () =>
      partial
        ? api.ticketArtifact(partial.ticketId, partial.artifactId)
        : Promise.reject(new Error("No artifact to load")),
    enabled: partial != null,
    staleTime: Infinity,
    // The app's `QueryCache.onError` toasts any query that fails with no data
    // (`api/queryClient.ts`). Not this one: the failure is rendered inline,
    // under the subtitle of the modal the operator is looking straight at, and
    // a toast behind the overlay is the wrong place for it. Suppressed, not
    // swallowed.
    meta: { suppressErrorToast: true },
  });

  const loadingRest = partial != null && !rest.isSuccess && !rest.isError;
  const failedRest = partial != null && rest.isError;

  const landed = rest.data?.content;
  useEffect(() => {
    if (!artifactId || !rest.isSuccess) return;
    completeDocument(artifactId, landed);
  }, [artifactId, rest.isSuccess, landed, completeDocument]);

  // Each document opens formatted; the raw toggle is a per-look decision.
  // Keyed on the document's *identity* rather than the object, because the
  // object is replaced when the rest of its content lands, and that must not
  // throw an operator who had switched to Source back to Formatted mid-read.
  const identity = document?.id ?? document;
  useEffect(() => {
    setRaw(false);
    setCopied(false);
  }, [identity]);

  if (!document) return null;

  const source = sourceText(document.content);

  async function copy() {
    // `aria-disabled` on a real <button> does not stop a click: an operator who
    // clicks anyway must not be handed a prefix as the whole output.
    if (loadingRest) return;
    try {
      await navigator.clipboard.writeText(source);
      setCopied(true);
    } catch (error) {
      pushToast({
        tone: "error",
        title: "Could not copy",
        message: describeError(error, "The clipboard refused the write — select the text instead."),
      });
    }
  }

  const copyLabel = copied ? "Copied" : loadingRest ? "Copy…" : failedRest && partial ? copyName(partial) : "Copy";

  return (
    <>
      <div className="modal-overlay reader-overlay" onClick={close} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel reader-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="reader-title"
        tabIndex={-1}
      >
        <header className="reader-header">
          <div className="reader-heading">
            <h2 id="reader-title" className="reader-title">
              {document.title}
              {document.badge ? (
                <span className={`reader-badge reader-badge--${document.badge.tone}`}>{document.badge.text}</span>
              ) : null}
            </h2>
            {document.subtitle && <p className="reader-subtitle">{document.subtitle}</p>}
            {failedRest ? (
              // Inline, not a toast: this is a modal over an overlay and the
              // operator is looking straight at the thing that failed.
              <div className="reader-fill reader-fill--error" role="alert">
                <span>
                  Could not load the rest of this output.{" "}
                  {describeError(rest.error, "The artifact request failed.")}
                </span>
                <Button variant="secondary" compact onClick={() => void rest.refetch()}>
                  Try again
                </Button>
              </div>
            ) : loadingRest && partial ? (
              <div className="reader-fill" role="status">
                {fillLine(partial)}
              </div>
            ) : null}
          </div>
          <div className="reader-actions">
            <button
              type="button"
              className="tab-btn"
              aria-pressed={raw}
              onClick={() => setRaw((value) => !value)}
            >
              {raw ? "Formatted" : "Source"}
            </button>
            <Button
              variant="plain"
              className="tab-btn"
              onClick={copy}
              aria-disabled={loadingRest ? true : undefined}
              aria-label={loadingRest ? "Copy — waiting for the rest of this output" : undefined}
            >
              {copyLabel}
            </Button>
            <IconCloseButton onClick={close} />
          </div>
        </header>
        <div className="reader-body">
          <div className="reader-measure">
            {raw ? <pre className="sc-raw reader-source">{source}</pre> : <StructuredContent value={document.content} />}
          </div>
        </div>
      </div>
    </>
  );
}
