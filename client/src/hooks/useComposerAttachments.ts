import { useCallback, useState } from "react";

import { pushToast } from "../state/toastStore";

/**
 * Mirrors `services/chat_attachments.py`. The server is the authority and
 * refuses anything these let through; checking here too means a wrong file is
 * named the moment it is picked, not after the operator has written a message.
 */
export const IMAGE_MIME_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"] as const;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_TEXT_BYTES = 512 * 1024;
export const MAX_ATTACHMENTS_PER_TURN = 8;
const TEXT_EXTENSIONS = new Set([
  "c", "cfg", "conf", "cpp", "cs", "css", "csv", "diff", "go", "h", "html", "ini", "java",
  "js", "json", "jsx", "kt", "log", "md", "patch", "py", "rb", "rs", "sh", "sql", "svg",
  "swift", "toml", "ts", "tsx", "txt", "xml", "yaml", "yml",
]);

/** What the file picker offers; the checks below still decide. */
export const ATTACHMENT_ACCEPT = [
  ...IMAGE_MIME_TYPES,
  "text/*",
  ...[...TEXT_EXTENSIONS].map((ext) => `.${ext}`),
].join(",");

export type PendingAttachmentKind = "text" | "image";

export interface PendingAttachment {
  /** Local only — the server id arrives with the upload, at send time. */
  key: string;
  file: File;
  kind: PendingAttachmentKind;
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

/** The kind the server will assign, or the reason it will refuse the file. */
export function classifyAttachment(file: File): { kind: PendingAttachmentKind } | { error: string } {
  const type = file.type.toLowerCase();
  const isImage = (IMAGE_MIME_TYPES as readonly string[]).includes(type);
  const isText = !isImage && (type.startsWith("text/") || TEXT_EXTENSIONS.has(extensionOf(file.name)));
  if (!isImage && !isText) {
    return { error: `${file.name} isn't a text file or a PNG, JPEG, GIF or WebP image.` };
  }
  if (file.size === 0) return { error: `${file.name} is empty.` };
  const limit = isImage ? MAX_IMAGE_BYTES : MAX_TEXT_BYTES;
  if (file.size > limit) {
    return {
      error: `${file.name} is ${Math.ceil(file.size / 1024)} KB; ${isImage ? "images" : "text files"} are limited to ${limit / 1024} KB.`,
    };
  }
  return { kind: isImage ? "image" : "text" };
}

let nextKey = 0;

export interface ComposerAttachmentsBinding {
  items: PendingAttachment[];
  add: (files: Iterable<File>) => void;
  remove: (key: string) => void;
  clear: () => void;
}

/** The files waiting to go out with the next message. */
export function useComposerAttachments(): ComposerAttachmentsBinding {
  const [items, setItems] = useState<PendingAttachment[]>([]);

  const add = useCallback((files: Iterable<File>) => {
    const refused: string[] = [];
    const accepted: PendingAttachment[] = [];
    for (const file of files) {
      const verdict = classifyAttachment(file);
      if ("error" in verdict) {
        refused.push(verdict.error);
        continue;
      }
      nextKey += 1;
      accepted.push({ key: `attachment-${nextKey}`, file, kind: verdict.kind });
    }
    // Read from the render's `items`, not inside a state updater: the updater
    // runs later, after the toast below would already have been decided.
    const room = Math.max(0, MAX_ATTACHMENTS_PER_TURN - items.length);
    if (accepted.length > room) {
      refused.push(
        `A message can carry ${MAX_ATTACHMENTS_PER_TURN} files; ${accepted.length - room} were left off.`,
      );
    }
    if (room > 0 && accepted.length) {
      setItems([...items, ...accepted.slice(0, room)]);
    }
    if (refused.length) {
      pushToast({ tone: "warning", title: "Couldn't attach", message: refused.join(" ") });
    }
  }, [items]);

  const remove = useCallback((key: string) => {
    setItems((current) => current.filter((item) => item.key !== key));
  }, []);

  const clear = useCallback(() => setItems([]), []);

  return { items, add, remove, clear };
}
