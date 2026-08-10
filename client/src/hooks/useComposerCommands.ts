import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  composerApi,
  type ComposerNote,
  type EditorPathMatch,
} from "../api/composerApi";
import { api } from "../api/client";
import {
  BUILTIN_COMMANDS,
  activeTrigger,
  applyCompletion,
  matchCommands,
  parseDraft,
  resolveCommand,
  skillCommands,
  type ComposerCommand,
  type ComposerTrigger,
} from "../lib/composerCommands";
import {
  composerQueueKey,
  useComposerQueueStore,
  type QueuedComposerMessage,
} from "../state/composerQueueStore";

/** How long typing settles before the `@` picker asks the server for paths. */
const MENTION_DEBOUNCE_MS = 120;

export type ComposerMenuItem =
  | { kind: "command"; id: string; command: ComposerCommand }
  | { kind: "path"; id: string; match: EditorPathMatch };

/** Any composer input this can drive — the bar uses an input, panels a textarea. */
type ComposerInput = HTMLInputElement | HTMLTextAreaElement;

export interface UseComposerCommandsOptions {
  value: string;
  onChange: (value: string) => void;
  /** Workspace the `@` picker searches and notes belong to. "" disables both. */
  workspaceSlug: string;
  /** Conversation identity for `/queue`; null when this surface cannot queue. */
  queueKey: string | null;
  /** True while the bound conversation is working — the queue drains on false. */
  isBusy: boolean;
  /** Send for real. `skill` is "" unless the draft opened with a `/skill`. */
  onSend: (content: string, skill: string) => void;
  /**
   * Whether this conversation honours a `/skill`.
   *
   * Only the Home Baxter turn carries one to the agent. Elsewhere the skills
   * are left out of the menu entirely rather than offered and ignored.
   */
  skillsEnabled?: boolean;
  /** Start a fresh conversation and send into it; omit to hide that note action. */
  onSendInNewChat?: (content: string) => void;
}

export interface ComposerCommandsBinding {
  inputRef: React.RefObject<ComposerInput | null>;
  /** Menu items, or [] when no trigger is open. */
  items: ComposerMenuItem[];
  activeIndex: number;
  setActiveIndex: (index: number) => void;
  /** What opened the menu, for labelling. */
  triggerKind: "slash" | "mention" | null;
  isOpen: boolean;
  accept: (item: ComposerMenuItem) => void;
  close: () => void;
  /** Call from the input's onChange, with the event's element. */
  handleChange: (value: string, element: ComposerInput | null) => void;
  /** Returns true when the key was consumed by the menu — skip your own handling. */
  handleKeyDown: (event: React.KeyboardEvent<ComposerInput>) => boolean;
  /** Interpret the draft and act. Returns true when it handled the send. */
  submit: () => boolean;
  notes: ComposerNote[];
  /** A `/note` with no text yet — persisted once it has some. */
  draftNote: string | null;
  setDraftNote: (body: string | null) => void;
  saveNote: (body: string) => void;
  updateNote: (id: string, body: string) => void;
  deleteNote: (id: string) => void;
  sendNote: (note: ComposerNote) => void;
  sendNoteInNewChat: ((note: ComposerNote) => void) | null;
  queued: QueuedComposerMessage[];
  cancelQueued: (id: string) => void;
}

function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/**
 * `/` commands and `@` references for one composer.
 *
 * Owns the completion menu, what a leading `/command` means at send time, the
 * `/queue` drain, and the `/note` post-its. The composer keeps its draft; this
 * only rewrites it when a completion is accepted.
 */
export function useComposerCommands({
  value,
  onChange,
  workspaceSlug,
  queueKey,
  isBusy,
  onSend,
  onSendInNewChat,
  skillsEnabled = false,
}: UseComposerCommandsOptions): ComposerCommandsBinding {
  const qc = useQueryClient();
  const inputRef = useRef<ComposerInput | null>(null);
  const [caret, setCaret] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [draftNote, setDraftNote] = useState<string | null>(null);
  // Set when a completion rewrites the draft; applied once React has rendered
  // the new value, or the browser drops the caret at the end of the text.
  const pendingCaret = useRef<number | null>(null);

  const enqueue = useComposerQueueStore((s) => s.enqueue);
  const dequeue = useComposerQueueStore((s) => s.dequeue);
  const removeQueued = useComposerQueueStore((s) => s.remove);
  const queued = useComposerQueueStore((s) => (queueKey ? s.queues[queueKey] : undefined)) ?? [];

  const skillsQuery = useQuery({
    queryKey: ["agent-skills"],
    queryFn: () => api.skills(),
    enabled: skillsEnabled,
    staleTime: 5 * 60_000,
  });
  // A command whose prerequisite is missing is left out rather than offered and
  // ignored — the same rule the skills follow. `/queue` needs a conversation to
  // queue into; `/note` needs a workspace to belong to.
  const available = useMemo(() => {
    const supported: Record<string, boolean> = {
      queue: Boolean(queueKey),
      note: Boolean(workspaceSlug),
    };
    return BUILTIN_COMMANDS.filter((command) => supported[command.name] ?? true);
  }, [queueKey, workspaceSlug]);
  const commands = useMemo(
    () => [...available, ...skillCommands(skillsEnabled ? skillsQuery.data : [])],
    [available, skillsQuery.data, skillsEnabled],
  );

  const trigger: ComposerTrigger | null = dismissed ? null : activeTrigger(value, caret);
  const mentionQuery = trigger?.kind === "mention" ? trigger.query : null;
  const settledMention = useDebounced(mentionQuery, MENTION_DEBOUNCE_MS);
  const pathsQuery = useQuery({
    queryKey: ["composer-paths", workspaceSlug, settledMention ?? ""],
    queryFn: () => composerApi.editorSearch(workspaceSlug, settledMention ?? ""),
    enabled: Boolean(workspaceSlug) && settledMention !== null,
    staleTime: 30_000,
  });

  const notesQuery = useQuery({
    queryKey: ["composer-notes", workspaceSlug],
    queryFn: () => composerApi.notes(workspaceSlug),
    enabled: Boolean(workspaceSlug),
  });
  const invalidateNotes = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["composer-notes", workspaceSlug] });
  }, [qc, workspaceSlug]);

  const createNote = useMutation({
    meta: { errorTitle: "Save note" },
    mutationFn: (body: string) => composerApi.createNote(workspaceSlug, body),
    onSuccess: invalidateNotes,
  });
  const patchNote = useMutation({
    meta: { errorTitle: "Update note" },
    mutationFn: ({ id, ...body }: { id: string; body?: string; mark_sent?: boolean }) =>
      composerApi.updateNote(workspaceSlug, id, body),
    onSuccess: invalidateNotes,
  });
  const removeNote = useMutation({
    meta: { errorTitle: "Delete note" },
    mutationFn: (id: string) => composerApi.deleteNote(workspaceSlug, id),
    onSuccess: invalidateNotes,
  });

  const items = useMemo<ComposerMenuItem[]>(() => {
    if (!trigger) return [];
    if (trigger.kind === "slash") {
      return matchCommands(commands, trigger.query).map((command) => ({
        kind: "command" as const,
        id: `command:${command.name}`,
        command,
      }));
    }
    // Results lag the keystroke by the debounce; showing the previous query's
    // hits beats blanking the menu on every character.
    return (pathsQuery.data ?? []).map((match) => ({
      kind: "path" as const,
      id: `path:${match.repo_path}`,
      match,
    }));
  }, [trigger, commands, pathsQuery.data]);

  // A changed result set invalidates the highlight — keeping index 3 while the
  // list shrinks to two entries selects nothing on Enter.
  useEffect(() => {
    setActiveIndex(0);
  }, [trigger?.kind, trigger?.query]);

  useLayoutEffect(() => {
    const position = pendingCaret.current;
    if (position === null) return;
    pendingCaret.current = null;
    const element = inputRef.current;
    if (!element) return;
    element.focus();
    element.setSelectionRange(position, position);
    setCaret(position);
  }, [value]);

  const close = useCallback(() => setDismissed(true), []);

  const handleChange = useCallback(
    (next: string, element: ComposerInput | null) => {
      setDismissed(false);
      setCaret(element?.selectionStart ?? next.length);
      onChange(next);
    },
    [onChange],
  );

  const accept = useCallback(
    (item: ComposerMenuItem) => {
      if (!trigger) return;
      const [insert, trailing] =
        item.kind === "command"
          ? [`/${item.command.name}`, " "]
          : [
              `@${item.match.repo_path}`,
              // A directory stays open so the next keystroke narrows inside it.
              item.match.kind === "directory" ? "/" : " ",
            ];
      const result = applyCompletion(value, trigger, insert, trailing);
      pendingCaret.current = result.caret;
      onChange(result.value);
    },
    [trigger, value, onChange],
  );

  const isOpen = Boolean(trigger) && items.length > 0;

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<ComposerInput>): boolean => {
      if (event.key === "Escape" && trigger) {
        event.preventDefault();
        close();
        return true;
      }
      if (!isOpen) return false;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveIndex((index) => (index + 1) % items.length);
        return true;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveIndex((index) => (index - 1 + items.length) % items.length);
        return true;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        const item = items[activeIndex] ?? items[0];
        if (item) accept(item);
        return true;
      }
      return false;
    },
    [isOpen, items, activeIndex, accept, close, trigger],
  );

  const reset = useCallback(() => {
    onChange("");
    setCaret(0);
    setDismissed(false);
  }, [onChange]);

  const submit = useCallback((): boolean => {
    const { command, body } = parseDraft(value);
    const resolved = resolveCommand(commands, command);
    if (!resolved) return false;

    if (resolved.kind === "skill") {
      const text = body.trim();
      if (!text) return true;
      onSend(text, resolved.name);
      reset();
      return true;
    }
    if (resolved.name === "queue") {
      const text = body.trim();
      // Nothing to queue, and no conversation to queue into, are both no-ops
      // rather than a silent ordinary send.
      if (!text || !queueKey) return true;
      enqueue(queueKey, text);
      reset();
      return true;
    }
    // `/note` with no text opens an empty post-it to type into; it persists
    // once it has something to persist.
    setDraftNote(body.trim());
    reset();
    return true;
  }, [value, commands, onSend, reset, queueKey, enqueue]);

  // The drain. One message per idle transition, and only from the surface that
  // owns this conversation's send — two composers sharing a key would double up.
  const sendRef = useRef(onSend);
  sendRef.current = onSend;
  useEffect(() => {
    if (!queueKey || isBusy || queued.length === 0) return;
    const next = dequeue(queueKey);
    if (next) sendRef.current(next.content, next.skill);
  }, [queueKey, isBusy, queued.length, dequeue]);

  const saveNote = useCallback(
    (body: string) => {
      const text = body.trim();
      if (!text || !workspaceSlug) return;
      createNote.mutate(text);
      setDraftNote(null);
    },
    [createNote, workspaceSlug],
  );

  const sendNote = useCallback(
    (note: ComposerNote) => {
      onSend(note.body, "");
      patchNote.mutate({ id: note.id, mark_sent: true });
    },
    [onSend, patchNote],
  );

  const sendNoteInNewChat = useMemo(() => {
    if (!onSendInNewChat) return null;
    return (note: ComposerNote) => {
      onSendInNewChat(note.body);
      patchNote.mutate({ id: note.id, mark_sent: true });
    };
  }, [onSendInNewChat, patchNote]);

  return {
    inputRef,
    items,
    activeIndex,
    setActiveIndex,
    triggerKind: trigger?.kind ?? null,
    isOpen,
    accept,
    close,
    handleChange,
    handleKeyDown,
    submit,
    notes: notesQuery.data ?? [],
    draftNote,
    setDraftNote,
    saveNote,
    updateNote: (id, body) => patchNote.mutate({ id, body }),
    deleteNote: (id) => removeNote.mutate(id),
    sendNote,
    sendNoteInNewChat,
    queued,
    cancelQueued: (id) => {
      if (queueKey) removeQueued(queueKey, id);
    },
  };
}

export { composerQueueKey };
