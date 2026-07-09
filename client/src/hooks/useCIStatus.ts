import { useEffect, useState } from "react";
import { api, type CIRunResult, type AutoFixAttempt } from "../api/client";

export interface CIStatusData {
  ciStatus: CIRunResult | null;
  autoFixHistory: AutoFixAttempt[];
  loading: boolean;
  error: string | null;
}

/**
 * Hook to fetch and poll CI status for a ticket.
 *
 * Polls every 10 seconds while CI is pending/failing.
 * Stops polling once CI passes.
 */
export function useCIStatus(ticketId: string): CIStatusData {
  const [ciStatus, setCIStatus] = useState<CIRunResult | null>(null);
  const [autoFixHistory, setAutoFixHistory] = useState<AutoFixAttempt[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch CI status
  const fetchCIStatus = async () => {
    try {
      setLoading(true);
      setError(null);

      const { ci_status, auto_fix_history } = await api.ciStatus(ticketId);

      setCIStatus(ci_status);
      setAutoFixHistory(auto_fix_history || []);
    } catch (err) {
      // CI endpoint may not exist yet (not all tickets have CI), don't treat as error
      console.debug("CI status not available for ticket", ticketId);
      setCIStatus(null);
      setAutoFixHistory([]);
    } finally {
      setLoading(false);
    }
  };

  // Initial fetch
  useEffect(() => {
    fetchCIStatus();
  }, [ticketId]);

  // Poll while CI is pending or failing
  useEffect(() => {
    if (!ciStatus || ciStatus.status === "passing" || ciStatus.status === "skipped") {
      return; // Stop polling
    }

    const interval = setInterval(() => {
      fetchCIStatus();
    }, 10_000); // Poll every 10 seconds

    return () => clearInterval(interval);
  }, [ciStatus?.status, ticketId]);

  return { ciStatus, autoFixHistory, loading, error };
}

/**
 * Hook to trigger manual auto-fix.
 */
export function useAutoFix(ticketId: string) {
  const [isFixing, setIsFixing] = useState(false);
  const [fixError, setFixError] = useState<string | null>(null);

  const triggerManualAutoFix = async () => {
    try {
      setIsFixing(true);
      setFixError(null);
      await api.triggerAutoFix(ticketId);
      // Poll will pick up the new attempt
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to trigger auto-fix";
      setFixError(message);
      console.error("Error triggering auto-fix:", err);
    } finally {
      setIsFixing(false);
    }
  };

  const skipCICheck = async () => {
    try {
      setIsFixing(true);
      setFixError(null);
      await api.skipCICheck(ticketId);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to skip CI check";
      setFixError(message);
      console.error("Error skipping CI check:", err);
    } finally {
      setIsFixing(false);
    }
  };

  return {
    triggerManualAutoFix,
    skipCICheck,
    isFixing,
    fixError,
  };
}
