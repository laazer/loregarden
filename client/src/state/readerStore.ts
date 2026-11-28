import { create } from "zustand";

/**
 * The one place long prose opens to be read.
 *
 * Markdown reaches the operator through a dozen narrow surfaces — a chat
 * bubble, an approval card, a 360px artifact box, the memory side panel — and
 * each was sized for a glance, not for a two-page review. Any of them can hand
 * its content here; `ReaderHost`, mounted once in `AppLayout`, renders it at a
 * width and size meant for reading.
 *
 * `content` is a string (markdown) or any JSON value (an artifact body), which
 * `StructuredContent` lays out as sections instead of a JSON dump.
 */
export interface ReaderDocument {
  /**
   * Stable identity for the thing being read — an artifact's id. Not the object:
   * a document can be handed its full content after it opens (see `partial`),
   * and anything keyed on object identity would treat that as a new document.
   */
  id?: string;
  title: string;
  /** One line under the title: kind, agent, age — whatever places the document. */
  subtitle?: string;
  /** What the document concluded, beside its title — a report's verdict, a checklist's score. */
  badge?: ReaderBadge;
  content: unknown;
  /**
   * Set when `content` is only the head of the document: the feed that handed it
   * over bounds a row's content, and the whole of it is one request away.
   * `ReaderHost` fetches the rest and calls `completeDocument`.
   */
  partial?: ReaderPartial;
}

/** Where the rest of a shortened document lives, and how much of it is missing. */
export interface ReaderPartial {
  ticketId: string;
  artifactId: string;
  /** UTF-8 size of what is on screen; absent when the row arrived unmeasured. */
  shownBytes?: number;
  /** The full stored size. */
  totalBytes: number;
}

export interface ReaderBadge {
  text: string;
  tone: "good" | "bad" | "warn";
}

interface ReaderState {
  document: ReaderDocument | null;
  open: (document: ReaderDocument) => void;
  /**
   * Fill the open document in with the whole of its content.
   *
   * A no-op unless `id` is still the open document's id. The fetch that resolves
   * here was started for a document the operator may have closed or navigated
   * away from since, and a late response must be discarded rather than reopen
   * the reader or overwrite the document now showing.
   */
  completeDocument: (id: string, content: unknown) => void;
  close: () => void;
}

export const useReaderStore = create<ReaderState>((set) => ({
  document: null,
  open: (document) => set({ document }),
  completeDocument: (id, content) =>
    set((state) =>
      state.document && state.document.id === id
        ? { document: { ...state.document, content } }
        : state,
    ),
  close: () => set({ document: null }),
}));

export function openReader(document: ReaderDocument): void {
  useReaderStore.getState().open(document);
}
