/**
 * The sidebar's data: one ranked list of entries, the views they point at, and
 * the writes that change either.
 *
 * The server owns the ranking. Positions are relative and non-contiguous, so
 * every write here re-reads rather than recomputing an order locally, and a
 * reorder always sends the complete permutation across both sections.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { ApiError } from "../api/http";
import {
  deleteView,
  fetchSidebarEntries,
  fetchViews,
  pinPage,
  reorderSidebarEntries,
  unpinEntry,
  updateView,
  type SidebarEntry,
  type ViewSummary,
} from "../lib/viewsApi";
import { toastActionFailed } from "../state/toastStore";

/** A lost race is worth re-issuing; a losing streak is not worth spinning on. */
const REORDER_RETRY_LIMIT = 3;

/** One resume for a seed that died part-way; a workspace that keeps refusing is not seedable. */
const SEED_ATTEMPT_LIMIT = 2;

function isContention(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/**
 * The permutation to re-send after a peer changed the sidebar underneath one.
 *
 * The user's intent is the relative order they asked for, so that survives;
 * entries the peer removed drop out, and entries it added land at the end —
 * where a fresh pin lands anyway.
 */
function rankAgainst(desired: string[], current: SidebarEntry[]): string[] {
  const live = new Set(current.map((entry) => entry.id));
  const kept = desired.filter((entryId) => live.has(entryId));
  const known = new Set(kept);
  return [...kept, ...current.map((entry) => entry.id).filter((entryId) => !known.has(entryId))];
}

/** Per-workspace seeding state: latched at the first read, then run to completion. */
interface SeedState {
  /** The first successful read was empty — this workspace has never been set up. */
  seedable: boolean;
  attempts: number;
  done: boolean;
  inFlight: boolean;
}

export interface SidebarTabs {
  /** Every entry, in the server's order — including any this client cannot draw. */
  entries: SidebarEntry[];
  viewsById: Map<string, ViewSummary>;
  /** Both reads have landed, so a row can be drawn without guessing its title. */
  isReady: boolean;
  pinPageKey: (pageKey: string) => void;
  unpinPageEntry: (entryId: string) => void;
  /**
   * Swap two entries and send the whole permutation. The caller picks the pair,
   * because which entry is a row's visible neighbour depends on the section it
   * is drawn in, and that is the component's knowledge, not this hook's.
   */
  swapEntries: (entryId: string, otherId: string) => void;
  /** Drop one entry onto another's place, sections included. */
  dropEntry: (draggedId: string, targetId: string) => void;
  renameView: (viewId: string, title: string) => void;
  closeView: (viewId: string) => void;
}

export function useSidebarTabs(workspaceSlug: string, seedPageKeys: string[]): SidebarTabs {
  const qc = useQueryClient();
  const enabled = Boolean(workspaceSlug);

  const entriesKey = useMemo(() => ["sidebar-entries", workspaceSlug], [workspaceSlug]);
  const viewsKey = useMemo(() => ["views", workspaceSlug], [workspaceSlug]);

  const entriesQuery = useQuery({
    queryKey: entriesKey,
    queryFn: () => fetchSidebarEntries(workspaceSlug),
    enabled,
  });
  const viewsQuery = useQuery({
    queryKey: viewsKey,
    queryFn: () => fetchViews(workspaceSlug),
    enabled,
  });

  const entries = useMemo(() => entriesQuery.data ?? [], [entriesQuery.data]);
  const viewsById = useMemo(
    () => new Map((viewsQuery.data ?? []).map((view) => [view.id, view])),
    [viewsQuery.data],
  );

  const invalidateEntries = useCallback(() => {
    qc.invalidateQueries({ queryKey: entriesKey });
  }, [qc, entriesKey]);

  const pin = useMutation({
    meta: { errorTitle: "Pin page" },
    mutationFn: (pageKey: string) => pinPage(workspaceSlug, pageKey),
    onSettled: invalidateEntries,
  });

  const unpin = useMutation({
    meta: { errorTitle: "Unpin page" },
    mutationFn: (entryId: string) => unpinEntry(workspaceSlug, entryId),
    onMutate: async (entryId) => {
      await qc.cancelQueries({ queryKey: entriesKey });
      const previous = qc.getQueryData<SidebarEntry[]>(entriesKey);
      qc.setQueryData<SidebarEntry[]>(entriesKey, (current) =>
        (current ?? []).filter((entry) => entry.id !== entryId),
      );
      return { previous };
    },
    onError: (_error, _entryId, context) => {
      // The server still has the entry; leaving the optimistic removal on
      // screen would report a deletion that did not happen.
      if (context?.previous) qc.setQueryData(entriesKey, context.previous);
    },
    onSettled: invalidateEntries,
  });

  const reorder = useMutation({
    // This mutation reports its own failure once, after the retries are spent;
    // the cache's blanket rule would report every losing attempt as well.
    meta: { suppressErrorToast: true },
    // The retry lives here rather than in react-query's `retry`, which can only
    // re-send the identical body. A 409 says a peer re-ranked first, and that
    // peer may have added or removed an entry — the server checks membership
    // before it ranks, so an unchanged body would come back a 400 blaming the
    // request. So each attempt re-reads and rebuilds the permutation.
    // A 400 is "fix the request": re-sending changes nothing.
    mutationFn: async (entryIds: string[]) => {
      let desired = entryIds;
      for (let attempt = 0; ; attempt += 1) {
        try {
          return await reorderSidebarEntries(workspaceSlug, desired);
        } catch (error) {
          if (!isContention(error) || attempt + 1 >= REORDER_RETRY_LIMIT) throw error;
          desired = rankAgainst(desired, await fetchSidebarEntries(workspaceSlug));
        }
      }
    },
    retry: false,
    onMutate: async (entryIds) => {
      await qc.cancelQueries({ queryKey: entriesKey });
      const previous = qc.getQueryData<SidebarEntry[]>(entriesKey);
      if (previous) {
        const byId = new Map(previous.map((entry) => [entry.id, entry]));
        const next = entryIds
          .map((entryId) => byId.get(entryId))
          .filter((entry): entry is SidebarEntry => entry !== undefined);
        qc.setQueryData<SidebarEntry[]>(entriesKey, next);
      }
      return { previous };
    },
    onError: (error, _entryIds, context) => {
      if (context?.previous) qc.setQueryData(entriesKey, context.previous);
      toastActionFailed("Reorder tabs", error);
    },
    onSettled: invalidateEntries,
  });

  const rename = useMutation({
    meta: { errorTitle: "Rename view" },
    mutationFn: (vars: { viewId: string; title: string }) =>
      updateView(workspaceSlug, vars.viewId, { title: vars.title }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: viewsKey });
    },
  });

  const close = useMutation({
    meta: { errorTitle: "Close tab" },
    // Deleting a view's sidebar entry is refused server-side: the view would be
    // stored, unranked and unreachable. Deleting the view drops both.
    mutationFn: (viewId: string) => deleteView(workspaceSlug, viewId),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: viewsKey });
      invalidateEntries();
    },
  });

  const seed = useMutation({
    meta: { errorTitle: "Set up sidebar" },
    // Sequentially, because position is assigned when a pin lands: firing all
    // seven at once ranks them in completion order, not the order asked for.
    mutationFn: async (pageKeys: string[]) => {
      for (const pageKey of pageKeys) {
        await pinPage(workspaceSlug, pageKey);
      }
    },
    onSettled: invalidateEntries,
  });

  /**
   * Only the *first* successful read of a workspace can tell "never set up"
   * from "the user emptied it", so the decision is latched there and never
   * re-derived: every later empty list — the seed's own refetch, an unpin of the
   * last entry, a deliberately bare sidebar on the next load — is a state the
   * user arrived at, not an invitation to pin seven pages into it.
   */
  const seedStates = useRef(new Map<string, SeedState>());
  const seedMutate = seed.mutate;
  const seedStatus = seed.status;
  useEffect(() => {
    if (!enabled || !entriesQuery.isSuccess) return;
    const states = seedStates.current;
    let state = states.get(workspaceSlug);
    if (!state) {
      state = { seedable: entries.length === 0, attempts: 0, done: false, inFlight: false };
      states.set(workspaceSlug, state);
    }
    if (!state.seedable || state.done || state.inFlight) return;
    if (state.attempts >= SEED_ATTEMPT_LIMIT || seedPageKeys.length === 0) return;
    // A pin that rejects part-way leaves the rest unpinned; `pinPage` is
    // idempotent, so the resume re-walks the list from the top and the pins that
    // already landed keep both their entry and their rank.
    const attempted = state;
    attempted.attempts += 1;
    attempted.inFlight = true;
    seedMutate(seedPageKeys, {
      onSuccess: () => {
        attempted.done = true;
      },
      onSettled: () => {
        attempted.inFlight = false;
      },
    });
  }, [
    enabled,
    entriesQuery.isSuccess,
    entries,
    workspaceSlug,
    seedPageKeys,
    seedMutate,
    seedStatus,
  ]);

  const swapEntries = useCallback(
    (entryId: string, otherId: string) => {
      const ids = entries.map((entry) => entry.id);
      const from = ids.indexOf(entryId);
      const to = ids.indexOf(otherId);
      if (from < 0 || to < 0 || from === to) return;
      ids[from] = otherId;
      ids[to] = entryId;
      reorder.mutate(ids);
    },
    [entries, reorder],
  );

  const dropEntry = useCallback(
    (draggedId: string, targetId: string) => {
      if (draggedId === targetId) return;
      const ids = entries.map((entry) => entry.id);
      const from = ids.indexOf(draggedId);
      const to = ids.indexOf(targetId);
      if (from < 0 || to < 0) return;
      ids.splice(from, 1);
      ids.splice(to, 0, draggedId);
      reorder.mutate(ids);
    },
    [entries, reorder],
  );

  const pinMutate = pin.mutate;
  const unpinMutate = unpin.mutate;
  const renameMutate = rename.mutate;
  const closeMutate = close.mutate;

  return {
    entries,
    viewsById,
    isReady: entriesQuery.data !== undefined && viewsQuery.data !== undefined,
    pinPageKey: useCallback((pageKey: string) => pinMutate(pageKey), [pinMutate]),
    unpinPageEntry: useCallback((entryId: string) => unpinMutate(entryId), [unpinMutate]),
    swapEntries,
    dropEntry,
    renameView: useCallback(
      (viewId: string, title: string) => renameMutate({ viewId, title }),
      [renameMutate],
    ),
    closeView: useCallback((viewId: string) => closeMutate(viewId), [closeMutate]),
  };
}
