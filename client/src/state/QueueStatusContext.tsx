/**
 * One queue subscription for the queue screen.
 *
 * The page used to hold three: the page, the dashboard and the visualization
 * each called `useParallelExecutionWS` for the same workspace, so a single
 * open tab meant three sockets and — whenever one of them was down — three
 * pollers.
 *
 * The workspace resolution that was inlined in QueuePage comes with it, since
 * there is exactly one answer to "which workspace is the queue showing" and
 * both the topbar controls and the body need it. `PageTopbar` portals those
 * controls into the topbar's DOM node while leaving them in this provider's
 * React subtree, which is what lets them share the subscription from up there.
 */

import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";

import { api } from "../api/client";
import { useParallelExecutionWS } from "../hooks/useParallelExecutionWS";
import type {
  ActiveRun,
  ParallelStats,
  QueuedRun,
  QueueEvent,
} from "../lib/queueSocket";
import { DEFAULT_PARALLEL_STATS } from "../lib/queueSocket";
import type { WorkspaceSummary } from "../api/types";
import { useUiStore } from "./uiStore";

export interface QueueStatusValue {
  workspace: WorkspaceSummary | null;
  workspaces: WorkspaceSummary[];
  workspacesLoading: boolean;
  activeSlug: string;
  setWorkspaceSlug: (slug: string) => void;

  activeRuns: ActiveRun[];
  queuedRuns: QueuedRun[];
  stats: ParallelStats;
  estimatedClearSeconds: number | null;
  isWebSocket: boolean;
  loading: boolean;

  /** Subscribe to forwarded queue events; returns an unsubscribe. */
  onQueueEvent: (listener: (event: QueueEvent) => void) => () => void;
}

const QueueStatusContext = createContext<QueueStatusValue | null>(null);

const EMPTY_VALUE: QueueStatusValue = {
  workspace: null,
  workspaces: [],
  workspacesLoading: false,
  activeSlug: "",
  setWorkspaceSlug: () => {},
  activeRuns: [],
  queuedRuns: [],
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
  const workspaceSlug = useUiStore((s) => s.workspace);
  const queueWorkspaceSlug = useUiStore((s) => s.queueWorkspaceSlug);
  const setQueueWorkspaceSlug = useUiStore((s) => s.setQueueWorkspaceSlug);

  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
  });

  const activeSlug = useMemo(() => {
    if (queueWorkspaceSlug) return queueWorkspaceSlug;
    // "all" is a console filter, not a workspace — the queue is per-workspace.
    if (workspaceSlug && workspaceSlug !== "all") return workspaceSlug;
    return workspaces.data?.[0]?.slug ?? "";
  }, [queueWorkspaceSlug, workspaceSlug, workspaces.data]);

  const workspace = useMemo(
    () => workspaces.data?.find((ws) => ws.slug === activeSlug) ?? null,
    [workspaces.data, activeSlug],
  );

  useEffect(() => {
    if (!queueWorkspaceSlug && activeSlug) {
      setQueueWorkspaceSlug(activeSlug);
    }
  }, [activeSlug, queueWorkspaceSlug, setQueueWorkspaceSlug]);

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

  const status = useParallelExecutionWS(
    workspace?.id ?? "",
    Boolean(workspace?.id),
    emit,
  );

  const value = useMemo<QueueStatusValue>(
    () => ({
      workspace,
      workspaces: workspaces.data ?? [],
      workspacesLoading: workspaces.isLoading,
      activeSlug,
      setWorkspaceSlug: setQueueWorkspaceSlug,
      activeRuns: status.activeRuns,
      queuedRuns: status.queuedRuns,
      stats: status.stats,
      estimatedClearSeconds: status.estimatedClearSeconds,
      isWebSocket: status.isWebSocket,
      loading: status.loading,
      onQueueEvent,
    }),
    [
      workspace,
      workspaces.data,
      workspaces.isLoading,
      activeSlug,
      setQueueWorkspaceSlug,
      status,
      onQueueEvent,
    ],
  );

  return <QueueStatusContext.Provider value={value}>{children}</QueueStatusContext.Provider>;
}
