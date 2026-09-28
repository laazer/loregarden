import { useEffect, useState } from "react";

import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";
import { useReaderStore } from "../../state/readerStore";
import { describeError, pushToast } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { StructuredContent } from "./StructuredContent";
import "./Reader.css";

function sourceText(content: unknown): string {
  return typeof content === "string" ? content : JSON.stringify(content, null, 2);
}

/**
 * The reading view `openReader` opens: a wide dialog, body text at reading
 * size on a ~72ch measure, with the raw source one toggle away for when the
 * formatting is the thing in question. Mounted once, in `AppLayout`.
 */
export function ReaderHost() {
  const document = useReaderStore((state) => state.document);
  const close = useReaderStore((state) => state.close);
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);

  useDialogDismiss(document ? close : null);

  // Each document opens formatted; the raw toggle is a per-look decision.
  useEffect(() => {
    setRaw(false);
    setCopied(false);
  }, [document]);

  if (!document) return null;

  const source = sourceText(document.content);

  async function copy() {
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
            </h2>
            {document.subtitle && <p className="reader-subtitle">{document.subtitle}</p>}
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
            <button type="button" className="tab-btn" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
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
