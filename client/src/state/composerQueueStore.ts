import { create } from "zustand";

/**
 * Messages waiting to be sent the moment their conversation goes idle — what
 * `/queue` (and its `/q` spelling) writes.
 *
 * Deliberately in memory rather than on the server: a queued message is a
 * message the operator is about to send, and one that outlived a reload would
 * fire into a conversation they have since walked away from. The flush happens
 * client-side too, from whichever composer owns the conversation.
 */
export interface QueuedComposerMessage {
  id: string;
  content: string;
  /** The skill picked alongside it, or "" — a queued turn keeps its `/skill`. */
  skill: string;
}

interface ComposerQueueState {
  /** Queue key (see `composerQueueKey`) → messages, oldest first. */
  queues: Record<string, QueuedComposerMessage[]>;
  enqueue: (key: string, content: string, skill?: string) => void;
  /** Remove and return the head, or null when the queue is empty. */
  dequeue: (key: string) => QueuedComposerMessage | null;
  remove: (key: string, id: string) => void;
  clear: (key: string) => void;
}

/**
 * One conversation's queue identity.
 *
 * The Home Baxter thread is keyed by workspace rather than by chat session id:
 * that id is "" until the first message creates a thread, and `/queue` before
 * the first send is exactly when the id does not exist yet.
 */
export function composerQueueKey(
  kind: string,
  id: string,
  workspaceSlug: string,
): string {
  if (kind === "baxter-home") return `baxter-home:${workspaceSlug}`;
  return `${kind}:${id}`;
}

let counter = 0;
function nextId(): string {
  counter += 1;
  return `queued-${counter}`;
}

export const useComposerQueueStore = create<ComposerQueueState>((set, get) => ({
  queues: {},
  enqueue: (key, content, skill = "") => {
    const text = content.trim();
    if (!text) return;
    set((state) => ({
      queues: {
        ...state.queues,
        [key]: [...(state.queues[key] ?? []), { id: nextId(), content: text, skill }],
      },
    }));
  },
  dequeue: (key) => {
    const queue = get().queues[key] ?? [];
    const head = queue[0];
    if (!head) return null;
    set((state) => ({
      queues: { ...state.queues, [key]: (state.queues[key] ?? []).slice(1) },
    }));
    return head;
  },
  remove: (key, id) =>
    set((state) => ({
      queues: {
        ...state.queues,
        [key]: (state.queues[key] ?? []).filter((entry) => entry.id !== id),
      },
    })),
  clear: (key) => set((state) => ({ queues: { ...state.queues, [key]: [] } })),
}));
