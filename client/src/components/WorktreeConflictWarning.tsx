/**
 * Warning component for displaying worktree merge conflicts.
 * Shows conflict details, severity, and resolution suggestions.
 */

import { useState } from 'react';
import { useWorktreeConflicts } from '../hooks/useWorktreeConflicts';
import type { ConflictFile } from '../hooks/useWorktreeConflicts';
import './WorktreeConflictWarning.css';

export interface WorktreeConflictWarningProps {
  worktreeId: string;
  runId?: string;
  onResolve?: () => void;
  onAbort?: () => void;
  compact?: boolean;
}

export function WorktreeConflictWarning({
  worktreeId,
  onResolve,
  onAbort,
  compact = false,
}: WorktreeConflictWarningProps) {
  const { conflicts, preview, hasConflicts, loading, error } =
    useWorktreeConflicts(worktreeId, 3000, true);
  const [expandedFile, setExpandedFile] = useState<string | null>(null);

  if (!hasConflicts && !loading) {
    return null; // Don't show component if no conflicts
  }

  if (loading) {
    return (
      <div className="conflict-warning loading">
        <span className="loading-spinner"></span>
        Checking for merge conflicts...
      </div>
    );
  }

  if (error) {
    return (
      <div className="conflict-warning error" data-testid="conflict-error">
        ⚠️ Error checking conflicts: {error}
      </div>
    );
  }

  if (!preview) {
    return null;
  }

  const getSeverityIcon = () => {
    switch (preview.severity) {
      case 'high':
        return '🚨';
      case 'medium':
        return '⚠️';
      case 'low':
        return 'ℹ️';
      default:
        return '⚠️';
    }
  };

  const getSeverityLabel = () => {
    switch (preview.severity) {
      case 'high':
        return 'Critical Conflicts';
      case 'medium':
        return 'Merge Conflicts';
      case 'low':
        return 'Potential Conflicts';
      default:
        return 'Merge Conflicts';
    }
  };

  const getFileIcon = (file: ConflictFile) => {
    switch (file.type) {
      case 'code':
        return '📝';
      case 'lock':
        return '🔒';
      case 'json':
        return '{}';
      case 'markdown':
        return '📄';
      default:
        return '📄';
    }
  };

  const conflictPercentage = preview.total_conflicts > 0
    ? Math.round((preview.auto_mergeable_count / preview.total_conflicts) * 100)
    : 0;

  return (
    <div
      className={`conflict-warning ${preview.severity} ${compact ? 'compact' : ''}`}
      data-testid="conflict-warning"
    >
      <div className="conflict-header">
        <div className="conflict-title">
          <span className="severity-icon">{getSeverityIcon()}</span>
          <span className="severity-label">{getSeverityLabel()}</span>
        </div>
        <div className="conflict-stats">
          <span className="conflict-count">{preview.total_conflicts} conflicts</span>
          <span className="conflict-auto-mergeable">
            {preview.auto_mergeable_count} auto-mergeable
          </span>
        </div>
      </div>

      {!compact && preview.total_conflicts > 0 && (
        <>
          <div className="conflict-progress">
            <div className="progress-bar">
              <div
                className="progress-fill auto-mergeable"
                style={{ width: `${conflictPercentage}%` }}
              ></div>
            </div>
            <span className="progress-label">{conflictPercentage}% auto-mergeable</span>
          </div>

          <div className="conflict-files" data-testid="conflict-files-list">
            {conflicts.map((file) => (
              <ConflictFileItem
                key={file.path}
                file={file}
                isExpanded={expandedFile === file.path}
                onToggle={() =>
                  setExpandedFile(expandedFile === file.path ? null : file.path)
                }
                icon={getFileIcon(file)}
              />
            ))}
          </div>
        </>
      )}

      {!compact && (
        <div className="conflict-actions">
          {onResolve && (
            <button className="action-button primary" onClick={onResolve}>
              🔧 Resolve Conflicts
            </button>
          )}
          {onAbort && (
            <button className="action-button secondary" onClick={onAbort}>
              ✕ Abort
            </button>
          )}
        </div>
      )}
    </div>
  );
}

interface ConflictFileItemProps {
  file: ConflictFile;
  isExpanded: boolean;
  onToggle: () => void;
  icon: string;
}

function ConflictFileItem({
  file,
  isExpanded,
  onToggle,
  icon,
}: ConflictFileItemProps) {
  return (
    <div className="conflict-file-item" data-testid={`conflict-file-${file.path}`}>
      <button className="file-toggle" onClick={onToggle}>
        <span className="toggle-icon">{isExpanded ? '▼' : '▶'}</span>
        <span className="file-icon">{icon}</span>
        <span className="file-path">{file.path}</span>
        {file.auto_mergeable && (
          <span className="file-badge auto-merge">Auto-mergeable</span>
        )}
      </button>

      {isExpanded && (
        <div className="file-details">
          <div className="detail-row">
            <span className="detail-label">Type:</span>
            <span className="detail-value">{file.type}</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Conflict Lines:</span>
            <span className="detail-value">{file.conflictLines}</span>
          </div>
          {file.resolution_suggestion && (
            <div className="detail-row">
              <span className="detail-label">Suggestion:</span>
              <span className="detail-value suggestion">
                {file.resolution_suggestion}
              </span>
            </div>
          )}
          {file.auto_mergeable && (
            <div className="detail-row">
              <span className="auto-merge-note">
                ✓ This file can be automatically merged
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
