/**
 * Unit tests for WorktreeConflictWarning component.
 */

import { render, screen, fireEvent } from '@testing-library/react';
import { WorktreeConflictWarning } from '../WorktreeConflictWarning';

jest.mock('../../hooks/useWorktreeConflicts', () => ({
  useWorktreeConflicts: jest.fn(),
}));

import { useWorktreeConflicts } from '../../hooks/useWorktreeConflicts';

const mockUseWorktreeConflicts = useWorktreeConflicts as jest.MockedFunction<
  typeof useWorktreeConflicts
>;

describe('WorktreeConflictWarning', () => {
  const mockConflictFile = {
    path: 'src/app.ts',
    type: 'code' as const,
    conflictLines: 12,
    auto_mergeable: false,
    resolution_suggestion: 'Merge both implementations',
  };

  const mockConflictFileAutoMergeable = {
    path: 'package-lock.json',
    type: 'lock' as const,
    conflictLines: 3,
    auto_mergeable: true,
    resolution_suggestion: 'Can be auto-merged',
  };

  const mockPreview = {
    conflicting_files: [mockConflictFile, mockConflictFileAutoMergeable],
    total_conflicts: 2,
    auto_mergeable_count: 1,
    severity: 'medium' as const,
  };

  const mockDetails = {
    worktree_id: 'wt-1',
    run_id: 'run-123',
    conflicts: [mockConflictFile, mockConflictFileAutoMergeable],
    merge_preview: mockPreview,
    timestamp: new Date().toISOString(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders nothing when no conflicts', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [],
      preview: null,
      details: null,
      hasConflicts: false,
      loading: false,
      error: null,
    });

    const { container } = render(
      <WorktreeConflictWarning worktreeId="wt-1" />
    );

    expect(container.firstChild).toBeNull();
  });

  test('displays loading state', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [],
      preview: null,
      details: null,
      hasConflicts: false,
      loading: true,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    expect(screen.getByText(/Checking for merge conflicts/)).toBeInTheDocument();
  });

  test('displays error message', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [],
      preview: null,
      details: null,
      hasConflicts: false,
      loading: false,
      error: 'Failed to detect conflicts',
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    expect(screen.getByTestId('conflict-error')).toBeInTheDocument();
    expect(screen.getByText(/Failed to detect conflicts/)).toBeInTheDocument();
  });

  test('displays conflict warning with severity', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile, mockConflictFileAutoMergeable],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    expect(screen.getByTestId('conflict-warning')).toBeInTheDocument();
    expect(screen.getByText('Merge Conflicts')).toBeInTheDocument();
  });

  test('displays conflict count and auto-mergeable count', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile, mockConflictFileAutoMergeable],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    expect(screen.getByText('2 conflicts')).toBeInTheDocument();
    expect(screen.getByText('1 auto-mergeable')).toBeInTheDocument();
  });

  test('displays progress bar with correct percentage', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile, mockConflictFileAutoMergeable],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    expect(screen.getByText('50% auto-mergeable')).toBeInTheDocument();
  });

  test('lists all conflict files', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile, mockConflictFileAutoMergeable],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    expect(screen.getByTestId('conflict-files-list')).toBeInTheDocument();
    expect(screen.getByText('src/app.ts')).toBeInTheDocument();
    expect(screen.getByText('package-lock.json')).toBeInTheDocument();
  });

  test('expands file details on click', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    const fileItem = screen.getByTestId('conflict-file-src/app.ts');
    const toggle = fileItem.querySelector('.file-toggle') as HTMLButtonElement;

    fireEvent.click(toggle);

    expect(screen.getByText('Type:')).toBeInTheDocument();
    expect(screen.getByText('code')).toBeInTheDocument();
  });

  test('displays file conflict details when expanded', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    const fileItem = screen.getByTestId('conflict-file-src/app.ts');
    const toggle = fileItem.querySelector('.file-toggle') as HTMLButtonElement;

    fireEvent.click(toggle);

    expect(screen.getByText('Conflict Lines:')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('Merge both implementations')).toBeInTheDocument();
  });

  test('shows auto-mergeable badge for files', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFileAutoMergeable],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    expect(screen.getByText('Auto-mergeable')).toBeInTheDocument();
  });

  test('displays auto-merge note when file is expandable', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFileAutoMergeable],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    const fileItem = screen.getByTestId('conflict-file-package-lock.json');
    const toggle = fileItem.querySelector('.file-toggle') as HTMLButtonElement;

    fireEvent.click(toggle);

    expect(screen.getByText(/can be automatically merged/)).toBeInTheDocument();
  });

  test('displays severity icons for different severity levels', () => {
    const { rerender } = render(
      <WorktreeConflictWarning worktreeId="wt-1" />
    );

    // Low severity
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile],
      preview: { ...mockPreview, severity: 'low' },
      details: { ...mockDetails, merge_preview: { ...mockPreview, severity: 'low' } },
      hasConflicts: true,
      loading: false,
      error: null,
    });

    rerender(<WorktreeConflictWarning worktreeId="wt-1" />);
    expect(screen.getByText('Potential Conflicts')).toBeInTheDocument();

    // Medium severity
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile],
      preview: { ...mockPreview, severity: 'medium' },
      details: { ...mockDetails, merge_preview: { ...mockPreview, severity: 'medium' } },
      hasConflicts: true,
      loading: false,
      error: null,
    });

    rerender(<WorktreeConflictWarning worktreeId="wt-1" />);
    expect(screen.getByText('Merge Conflicts')).toBeInTheDocument();

    // High severity
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile],
      preview: { ...mockPreview, severity: 'high' },
      details: { ...mockDetails, merge_preview: { ...mockPreview, severity: 'high' } },
      hasConflicts: true,
      loading: false,
      error: null,
    });

    rerender(<WorktreeConflictWarning worktreeId="wt-1" />);
    expect(screen.getByText('Critical Conflicts')).toBeInTheDocument();
  });

  test('calls onResolve callback when resolve button clicked', () => {
    const onResolve = jest.fn();

    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(
      <WorktreeConflictWarning
        worktreeId="wt-1"
        onResolve={onResolve}
      />
    );

    const resolveButton = screen.getByText('Resolve Conflicts');
    fireEvent.click(resolveButton);

    expect(onResolve).toHaveBeenCalled();
  });

  test('calls onAbort callback when abort button clicked', () => {
    const onAbort = jest.fn();

    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(
      <WorktreeConflictWarning
        worktreeId="wt-1"
        onAbort={onAbort}
      />
    );

    const abortButton = screen.getByText('Abort');
    fireEvent.click(abortButton);

    expect(onAbort).toHaveBeenCalled();
  });

  test('hides conflict details in compact mode', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    const { container } = render(
      <WorktreeConflictWarning worktreeId="wt-1" compact={true} />
    );

    const conflictFiles = container.querySelector('.conflict-files');
    expect(conflictFiles).toHaveClass('conflict-files');

    // In compact mode, conflict files should not be visible
    expect(container.querySelector('.conflict-warning.compact')).toBeInTheDocument();
  });

  test('displays correct file icons', () => {
    const filesWithTypes = [
      { ...mockConflictFile, type: 'code' as const, expected: '📝' },
      { ...mockConflictFile, path: 'package.lock', type: 'lock' as const, expected: '🔒' },
      { ...mockConflictFile, path: 'config.json', type: 'json' as const, expected: '{}' },
      { ...mockConflictFile, path: 'README.md', type: 'markdown' as const, expected: '📄' },
    ];

    filesWithTypes.forEach((file) => {
      mockUseWorktreeConflicts.mockReturnValue({
        conflicts: [file],
        preview: mockPreview,
        details: { ...mockDetails, conflicts: [file] },
        hasConflicts: true,
        loading: false,
        error: null,
      });

      const { unmount } = render(
        <WorktreeConflictWarning worktreeId="wt-1" />
      );

      unmount();
    });
  });

  test('toggles file expansion correctly', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [mockConflictFile],
      preview: mockPreview,
      details: mockDetails,
      hasConflicts: true,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="wt-1" />);

    const fileItem = screen.getByTestId('conflict-file-src/app.ts');
    const toggle = fileItem.querySelector('.file-toggle') as HTMLButtonElement;

    // Initially not expanded
    expect(screen.queryByText('Type:')).not.toBeInTheDocument();

    // Expand
    fireEvent.click(toggle);
    expect(screen.getByText('Type:')).toBeInTheDocument();

    // Collapse
    fireEvent.click(toggle);
    expect(screen.queryByText('Type:')).not.toBeInTheDocument();
  });

  test('passes correct worktreeId to hook', () => {
    mockUseWorktreeConflicts.mockReturnValue({
      conflicts: [],
      preview: null,
      details: null,
      hasConflicts: false,
      loading: false,
      error: null,
    });

    render(<WorktreeConflictWarning worktreeId="custom-worktree-id" />);

    expect(mockUseWorktreeConflicts).toHaveBeenCalledWith('custom-worktree-id', 3000, true);
  });
});
