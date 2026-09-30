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
  title: string;
  /** One line under the title: kind, agent, age — whatever places the document. */
  subtitle?: string;
  content: unknown;
}

interface ReaderState {
  document: ReaderDocument | null;
  open: (document: ReaderDocument) => void;
  close: () => void;
}

export const useReaderStore = create<ReaderState>((set) => ({
  document: null,
  open: (document) => set({ document }),
  close: () => set({ document: null }),
}));

export function openReader(document: ReaderDocument): void {
  useReaderStore.getState().open(document);
}
