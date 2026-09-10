/**
 * One docker-capacity poll shared by the board and the rail.
 *
 * Both surfaces show the same ledger — the rail summarises the ceiling, the
 * board draws who holds it and who is behind them — and two independent polls
 * would let the two halves of one screen disagree about the same moment. The
 * queue's own status has one subscription above the layout for exactly this
 * reason; this is the same rule at a smaller scale.
 *
 * Polls only while a consumer is mounted, which is only while the Docker tab is
 * selected. A failed read is kept distinct from an empty ledger: they look
 * identical if you only check whether the lists are empty.
 */

import { useCallback, useEffect, useState } from "react";

import { dockerApi } from "../api/dockerApi";
import type { DockerCapacityStatus } from "../api/dockerTypes";
import { describeError } from "../state/toastStore";

/** While the tab is open the ledger is worth re-reading; it is closed the rest of the time. */
const REFRESH_MS = 10_000;

export interface DockerCapacityState {
  status: DockerCapacityStatus | null;
  error: string;
  loading: boolean;
  reload: () => void;
}

export function useDockerCapacity(): DockerCapacityState {
  const [status, setStatus] = useState<DockerCapacityStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setStatus(await dockerApi.capacity());
      setError("");
    } catch (err) {
      setError(describeError(err, "Failed to read docker capacity"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  return { status, error, loading, reload: () => void load() };
}
