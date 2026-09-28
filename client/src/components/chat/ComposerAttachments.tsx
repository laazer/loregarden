import { useEffect, useMemo, useRef } from "react";

import { API_BASE, type ChatAttachment } from "../../api/client";
import { ATTACHMENT_ACCEPT, type PendingAttachment } from "../../hooks/useComposerAttachments";
import "./ComposerAttachments.css";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function FileGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

/** Paperclip that opens the file picker. The composer also takes paste and drop. */
export function ComposerAttachButton({
  onFiles,
  disabled = false,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        type="button"
        className="lg-composer-icon-btn"
        aria-label="Attach files"
        title="Attach files — or paste or drop them here"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
          <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
        </svg>
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ATTACHMENT_ACCEPT}
        className="lg-composer-file-input"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          // Cleared so picking the same file again still fires a change.
          e.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
    </>
  );
}

function PendingThumb({ file }: { file: File }) {
  const url = useMemo(() => URL.createObjectURL(file), [file]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  return <img className="lg-attachment-thumb" src={url} alt="" />;
}

/** The files waiting to go with the next message, each removable. */
export function ComposerAttachmentTray({
  items,
  onRemove,
  disabled = false,
}: {
  items: PendingAttachment[];
  onRemove: (key: string) => void;
  disabled?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <ul className="lg-attachment-tray" aria-label="Attached files">
      {items.map((item) => (
        <li key={item.key} className="lg-attachment-chip">
          {item.kind === "image" ? <PendingThumb file={item.file} /> : <FileGlyph />}
          <span className="lg-attachment-name" title={item.file.name}>
            {item.file.name}
          </span>
          <span className="lg-attachment-size">{formatSize(item.file.size)}</span>
          <button
            type="button"
            className="lg-attachment-remove"
            aria-label={`Remove ${item.file.name}`}
            disabled={disabled}
            onClick={() => onRemove(item.key)}
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden>
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </li>
      ))}
    </ul>
  );
}

function chatAttachmentUrl(
  workspaceSlug: string,
  sessionId: string,
  attachmentId: string,
): string {
  return `${API_BASE}/api/workspaces/${encodeURIComponent(workspaceSlug)}/baxter-chat/sessions/${sessionId}/attachments/${attachmentId}`;
}

/** What a sent user turn carried, under its bubble. */
export function MessageAttachments({
  attachments,
  workspaceSlug,
  sessionId,
}: {
  attachments: ChatAttachment[];
  workspaceSlug: string;
  sessionId: string;
}) {
  if (attachments.length === 0) return null;
  return (
    <ul className="lg-attachment-tray lg-attachment-tray--sent" aria-label="Files sent with this message">
      {attachments.map((attachment) => {
        const url = chatAttachmentUrl(workspaceSlug, sessionId, attachment.id);
        return (
          <li key={attachment.id} className="lg-attachment-chip">
            <a
              className="lg-attachment-link"
              href={url}
              target="_blank"
              rel="noreferrer"
              title={`Open ${attachment.name}`}
            >
              {attachment.kind === "image" ? (
                <img className="lg-attachment-thumb" src={url} alt="" loading="lazy" />
              ) : (
                <FileGlyph />
              )}
              <span className="lg-attachment-name">{attachment.name}</span>
              <span className="lg-attachment-size">{formatSize(attachment.size)}</span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}
