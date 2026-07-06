/**
 * React hook for worktree merge conflict detection and management.
 * Polls API for conflict detection, provides conflict details and resolution options.
 */

import { useEffect, useState } from 'react';

export interface ConflictFile {
  path: string;
  type: 'code' | 'lock' | 'json' | 'markdown' | 'other';
  conflictLines: number;
  auto_mergeable: boolean;
  resolution_suggestion?: string;
}

export interface ConflictPreview {
  conflicting_files: ConflictFile[];
  total_conflicts: number;
  auto_mergeable_count: number;
  severity: 'low' | 'medium' | 'high';
}

export interface WorktreeConflictDetails {
  worktree_id: string;
  run_id: string;
  conflicts: ConflictFile[];
  merge_preview: ConflictPreview;
  timestamp: string;
  error?: string;
}

export interface WorktreeConflictsStatus {
  conflicts: ConflictFile[];
  preview: ConflictPreview | null;
  details: WorktreeConflictDetails | null;
  hasConflicts: boolean;
  loading: boolean;
  error: string | null;
}

const DEFAULT_POLL_INTERVAL = 3000; // 3 seconds

export function useWorktreeConflicts(
  worktreeId: string,
  pollInterval: number = DEFAULT_POLL_INTERVAL,
  enabled: boolean = true
): WorktreeConflictsStatus {
  const [conflicts, setConflicts] = useState<ConflictFile[]>([]);
  const [preview, setPreview] = useState<ConflictPreview | null>(null);
  const [details, setDetails] = useState<WorktreeConflictDetails | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    let isMounted = true;
    let intervalId: ReturnType<typeof setInterval>;

    const fetchConflicts = async () => {
      try {
        const response = await fetch(
          `/api/parallel/conflicts/${worktreeId}`
        );

        if (!response.ok) {
          if (response.status === 404) {
            // No worktree found - clear conflicts
            if (isMounted) {
              setConflicts([]);
              setPreview(null);
              setDetails(null);
              setError(null);
            }
            return;
          }
          throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        if (isMounted) {
          setConflicts(data.conflicts || []);
          setPreview(data.merge_preview || null);
          setDetails(data);
          setError(null);
        }
      } catch (err) {
        if (isMounted) {
          const errorMessage = err instanceof Error ? err.message : 'Failed to fetch conflicts';
          // Only set error if it's not 404 (which means no conflicts)
          if (!errorMessage.includes('404')) {
            setError(errorMessage);
          }
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };

    // Initial fetch
    fetchConflicts();

    // Poll for updates
    intervalId = setInterval(fetchConflicts, pollInterval);

    return () => {
      isMounted = false;
      clearInterval(intervalId);
    };
  }, [worktreeId, pollInterval, enabled]);

  return {
    conflicts,
    preview,
    details,
    hasConflicts: conflicts.length > 0,
    loading,
    error,
  };
}
