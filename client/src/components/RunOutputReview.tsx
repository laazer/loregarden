/**
 * Line-by-line output review component
 * Allows commenting on specific lines of run stdout/stderr
 */

import { useState, useMemo } from 'react';
import './RunOutputReview.css';

export interface OutputLine {
  number: number;
  content: string;
  comments: OutputComment[];
}

export interface OutputComment {
  line_number: number;
  content: string;
  created_at: string;
  created_by?: string;
  resolved?: boolean;
}

export interface RunOutputReviewProps {
  outputType: 'stdout' | 'stderr';
  lines: OutputLine[];
  approved?: boolean;
  approvedBy?: string;
  onAddComment?: (lineNumber: number, content: string) => void;
  onApprove?: () => void;
  isLoading?: boolean;
}

export function RunOutputReview({
  outputType,
  lines = [],
  approved = false,
  approvedBy,
  onAddComment,
  onApprove,
  isLoading = false,
}: RunOutputReviewProps) {
  const [expandedLines, setExpandedLines] = useState<Set<number>>(
    new Set()
  );
  const [newCommentLine, setNewCommentLine] = useState<number | null>(null);
  const [newCommentContent, setNewCommentContent] = useState('');

  const handleToggleExpanded = (lineNumber: number) => {
    const next = new Set(expandedLines);
    if (next.has(lineNumber)) {
      next.delete(lineNumber);
    } else {
      next.add(lineNumber);
    }
    setExpandedLines(next);
  };

  const handleAddComment = (lineNumber: number) => {
    if (!newCommentContent.trim()) return;

    onAddComment?.(lineNumber, newCommentContent);
    setNewCommentContent('');
    setNewCommentLine(null);
  };

  const stats = useMemo(() => {
    let commentCount = 0;
    let linesWithComments = 0;

    lines.forEach((line) => {
      if (line.comments.length > 0) {
        linesWithComments++;
        commentCount += line.comments.length;
      }
    });

    return { commentCount, linesWithComments };
  }, [lines]);

  const typeLabel = outputType === 'stdout' ? 'STDOUT' : 'STDERR';
  const typeIcon = outputType === 'stdout' ? '📤' : '⚠️';

  return (
    <div className="run-output-review">
      {/* Header */}
      <div className="output-header">
        <div className="output-title">
          <span className="output-icon">{typeIcon}</span>
          <span className="output-type">{typeLabel}</span>
          <span className="output-stats">
            {lines.length} lines, {stats.commentCount} comment
            {stats.commentCount === 1 ? '' : 's'}
          </span>
        </div>

        {approved && (
          <div className="approval-badge">
            ✓ Approved by {approvedBy || 'system'}
          </div>
        )}
      </div>

      {/* Output Lines */}
      <div className="output-lines">
        {lines.length === 0 ? (
          <div className="no-output">
            <div className="no-output-icon">-</div>
            <div className="no-output-text">No output</div>
          </div>
        ) : (
          lines.map((line) => (
            <div key={line.number} className="output-line-group">
              <div
                className={`output-line ${
                  line.comments.length > 0 ? 'has-comments' : ''
                }`}
              >
                <div className="line-number">{line.number}</div>
                <div className="line-content">
                  <code>{line.content || ' '}</code>
                </div>

                {line.comments.length > 0 && (
                  <button
                    className="comment-indicator"
                    onClick={() => handleToggleExpanded(line.number)}
                    title={`${line.comments.length} comment${
                      line.comments.length === 1 ? '' : 's'
                    }`}
                  >
                    💬 {line.comments.length}
                  </button>
                )}
              </div>

              {/* Comments for this line */}
              {expandedLines.has(line.number) && line.comments.length > 0 && (
                <div className="line-comments">
                  {line.comments.map((comment, idx) => (
                    <div key={idx} className="comment-item">
                      <div className="comment-header">
                        <span className="comment-author">
                          {comment.created_by || 'Anonymous'}
                        </span>
                        <span className="comment-time">
                          {new Date(comment.created_at).toLocaleString()}
                        </span>
                        {comment.resolved && (
                          <span className="comment-resolved">Resolved</span>
                        )}
                      </div>
                      <div className="comment-content">{comment.content}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* Add comment form */}
              {newCommentLine === line.number && (
                <div className="add-comment-form">
                  <textarea
                    className="comment-textarea"
                    placeholder="Add a comment..."
                    value={newCommentContent}
                    onChange={(e) => setNewCommentContent(e.target.value)}
                    disabled={isLoading}
                    rows={3}
                  />

                  <div className="comment-form-actions">
                    <button
                      className="btn btn-comment-add"
                      onClick={() => handleAddComment(line.number)}
                      disabled={isLoading || !newCommentContent.trim()}
                    >
                      Comment
                    </button>
                    <button
                      className="btn btn-cancel"
                      onClick={() => {
                        setNewCommentLine(null);
                        setNewCommentContent('');
                      }}
                      disabled={isLoading}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {/* Add comment button */}
              {newCommentLine !== line.number && (
                <div className="line-actions">
                  <button
                    className="btn-add-comment"
                    onClick={() => setNewCommentLine(line.number)}
                    disabled={isLoading}
                  >
                    + Comment
                  </button>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Footer Actions */}
      {!approved && (
        <div className="output-footer">
          <button
            className="btn btn-approve-output"
            onClick={onApprove}
            disabled={isLoading}
          >
            ✓ Approve Output
          </button>
          {isLoading && <span className="loading-text">Processing...</span>}
        </div>
      )}
    </div>
  );
}
