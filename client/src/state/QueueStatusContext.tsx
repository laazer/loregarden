/**
 * One queue subscription for the queue screen.
 *
 * The page used to hold three: the page, the dashboard and the visualization
 * each called `useParallelExecutionWS`, so a single open tab meant three
 * sockets and — whenever one of them was down — three pollers.
 *
 * There is no workspace to resolve. The execution slots are one shared pool, so
 * the queue this reports is the whole machine's — the page used to pick a
 * workspace and show a slice, which made two workspaces look like two
 * independent queues when they were competing for the same three slots.
 *
 * The workspace *list* is still here: staging a ticket needs to offer tickets
 * from every workspace, and every card has to say which one it belongs to.
 * `PageTopbar` portals the topbar controls into the topbar's DOM node while
 * leaving them in this provider's React subtree, which is what lets them share
 * the subscription from up there.
 */

import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  type ReactNode,
} from "react";

import { api } from "../api/client";
import { useParallelExecutionWS } from "../hooks/useParallelExecutionWS";
import type {
  ActiveRun,
  ParallelStats,
  QueueLane,
  QueuedRun,
  QueueEvent,
} from "../lib/queueSocket";
import { DEFAULT_PARALLEL_STATS } from "../lib/queueSocket";
import type { WorkspaceSummary } from "../api/types";

export interface QueueStatusValue {
  workspaces: WorkspaceSummary[];
  workspacesLoading: boolean;

  activeRuns: ActiveRun[];
  queuedRuns: QueuedRun[];
  /** Each slot with what runs in it and what is queued behind it. */
  lanes: QueueLane[];
  stats: ParallelStats;
  estimatedClearSeconds: number | null;
  isWebSocket: boolean;
  loading: boolean;

  /** Subscribe to forwarded queue events; returns an unsubscribe. */
  onQueueEvent: (listener: (event: QueueEvent) => void) => () => void;
}

const QueueStatusContext = createContext<QueueStatusValue | null>(null);

const EMPTY_VALUE: QueueStatusValue = {
  workspaces: [],
  workspacesLoading: false,
  activeRuns: [],
  queuedRuns: [],
  lanes: [],
  stats: DEFAULT_PARALLEL_STATS,
  estimatedClearSeconds: null,
  isWebSocket: false,
  loading: false,
  onQueueEvent: () => () => {},
};

/**
 * Queue state, or an inert stand-in off the queue page.
 *
 * The topbar renders on every page and asks unconditionally; returning the
 * empty value rather than throwing keeps that a plain render instead of a
 * `queueActive &&` guard duplicated at each call site.
 */
export function useQueueStatus(): QueueStatusValue {
  return useContext(QueueStatusContext) ?? EMPTY_VALUE;
}

export function QueueStatusProvider({ children }: { children: ReactNode }) {
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
  });

  // A Set rather than a single handler: the toast stack and anything else that
  // wants events both get them, and neither has to know about the other.
  const listeners = useRef(new Set<(event: QueueEvent) => void>());

  const onQueueEvent = useCallback((listener: (event: QueueEvent) => void) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  const emit = useCallback((event: QueueEvent) => {
    for (const listener of listeners.current) listener(event);
  }, []);

  const status = useParallelExecutionWS(true, emit);

  const value = useMemo<QueueStatusValue>(
    () => ({
      workspaces: workspaces.data ?? [],
      workspacesLoading: workspaces.isLoading,
      activeRuns: status.activeRuns,
      queuedRuns: status.queuedRuns,
      lanes: status.lanes,
      stats: status.stats,
      estimatedClearSeconds: status.estimatedClearSeconds,
      isWebSocket: status.isWebSocket,
      loading: status.loading,
      onQueueEvent,
    }),
    [workspaces.data, workspaces.isLoading, status, onQueueEvent],
  );

  return <QueueStatusContext.Provider value={value}>{children}</QueueStatusContext.Provider>;
}
