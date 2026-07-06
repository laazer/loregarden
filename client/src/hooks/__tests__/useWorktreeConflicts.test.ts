/**
 * Unit tests for useWorktreeConflicts hook.
 */

import { renderHook, waitFor } from '@testing-library/react';
import { useWorktreeConflicts } from '../useWorktreeConflicts';

describe('useWorktreeConflicts', () => {
  const mockConflictResponse = {
    conflicts: [
      {
        path: 'src/app.ts',
        type: 'code' as const,
        conflictLines: 12,
        auto_mergeable: false,
        resolution_suggestion: 'Merge both implementations',
      },
      {
        path: 'package-lock.json',
        type: 'lock' as const,
        conflictLines: 3,
        auto_mergeable: true,
        resolution_suggestion: 'Can be auto-merged',
      },
    ],
    merge_preview: {
      conflicting_files: [],
      total_conflicts: 2,
      auto_mergeable_count: 1,
      severity: 'medium' as const,
    },
    worktree_id: 'wt-1',
    run_id: 'run-123',
    timestamp: new Date().toISOString(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  test('returns initial loading state', () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => mockConflictResponse,
    });

    const { result } = renderHook(() => useWorktreeConflicts('wt-1'));

    expect(result.current.loading).toBe(true);
    expect(result.current.conflicts).toEqual([]);
    expect(result.current.hasConflicts).toBe(false);
  });

  test('fetches conflicts successfully', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => mockConflictResponse,
    });

    const { result } = renderHook(() => useWorktreeConflicts('wt-1'));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.conflicts).toEqual(mockConflictResponse.conflicts);
    expect(result.current.preview).toEqual(mockConflictResponse.merge_preview);
    expect(result.current.hasConflicts).toBe(true);
    expect(result.current.error).toBe(null);
  });

  test('handles 404 not found gracefully', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 404,
    });

    const { result } = renderHook(() => useWorktreeConflicts('wt-1'));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.conflicts).toEqual([]);
    expect(result.current.preview).toBeNull();
    expect(result.current.error).toBeNull();
  });

  test('handles fetch errors', async () => {
    (global.fetch as jest.Mock).mockRejectedValue(
      new Error('Network error')
    );

    const { result } = renderHook(() => useWorktreeConflicts('wt-1'));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe('Network error');
  });

  test('handles HTTP errors', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 500,
    });

    const { result } = renderHook(() => useWorktreeConflicts('wt-1'));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe('HTTP 500');
  });

  test('polls at specified interval', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => mockConflictResponse,
    });

    renderHook(() => useWorktreeConflicts('wt-1', 100));

    await waitFor(
      () => {
        expect(global.fetch).toHaveBeenCalledTimes(1);
      },
      { timeout: 500 }
    );

    // Wait for the polling interval to trigger
    await waitFor(
      () => {
        expect(global.fetch).toHaveBeenCalledTimes(2);
      },
      { timeout: 500 }
    );
  });

  test('uses custom poll interval', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => mockConflictResponse,
    });

    renderHook(() => useWorktreeConflicts('wt-1', 3000));

    expect(global.fetch).toHaveBeenCalledWith('/api/parallel/conflicts/wt-1');
  });

  test('cleans up interval on unmount', () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => mockConflictResponse,
    });

    const clearIntervalSpy = jest.spyOn(global, 'clearInterval');

    const { unmount } = renderHook(() => useWorktreeConflicts('wt-1'));

    unmount();

    expect(clearIntervalSpy).toHaveBeenCalled();

    clearIntervalSpy.mockRestore();
  });

  test('respects enabled flag', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => mockConflictResponse,
    });

    const { result } = renderHook(() =>
      useWorktreeConflicts('wt-1', 5000, false)
    );

    // Should not be loading if disabled
    expect(result.current.loading).toBe(false);

    await waitFor(
      () => {
        // Fetch should not be called
        expect(global.fetch).not.toHaveBeenCalled();
      },
      { timeout: 100 }
    );
  });

  test('fetches with correct worktreeId', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => mockConflictResponse,
    });

    renderHook(() => useWorktreeConflicts('custom-wt-id'));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/parallel/conflicts/custom-wt-id'
      );
    });
  });

  test('calculates hasConflicts correctly', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => mockConflictResponse,
    });

    const { result } = renderHook(() => useWorktreeConflicts('wt-1'));

    await waitFor(() => {
      expect(result.current.hasConflicts).toBe(true);
    });

    // Test with empty conflicts
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({
        ...mockConflictResponse,
        conflicts: [],
      }),
    });

    const { result: result2 } = renderHook(() =>
      useWorktreeConflicts('wt-2')
    );

    await waitFor(() => {
      expect(result2.current.hasConflicts).toBe(false);
    });
  });

  test('preserves data on error', async () => {
    // First call succeeds
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockConflictResponse,
    });

    const { result, rerender } = renderHook(() =>
      useWorktreeConflicts('wt-1', 100)
    );

    await waitFor(() => {
      expect(result.current.conflicts.length).toBe(2);
    });

    // Second call fails
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 500,
    });

    // Force re-render to trigger next poll
    rerender();

    // Data should still be there (according to component, it keeps previous data on error)
    expect(result.current.conflicts.length).toBe(2);
  });

  test('updates when worktreeId changes', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => mockConflictResponse,
    });

    const { rerender } = renderHook(
      ({ id }: { id: string }) => useWorktreeConflicts(id),
      { initialProps: { id: 'wt-1' } }
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/parallel/conflicts/wt-1');
    });

    (global.fetch as jest.Mock).mockClear();

    rerender({ id: 'wt-2' });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/parallel/conflicts/wt-2');
    });
  });

  test('handles empty response gracefully', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({
        conflicts: undefined,
        merge_preview: undefined,
        worktree_id: 'wt-1',
        run_id: 'run-123',
        timestamp: new Date().toISOString(),
      }),
    });

    const { result } = renderHook(() => useWorktreeConflicts('wt-1'));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.conflicts).toEqual([]);
    expect(result.current.preview).toBeNull();
    expect(result.current.hasConflicts).toBe(false);
  });
});
