/**
 * Line-by-line review of a run's stdout/stderr.
 *
 * Laid out like InlineCodeDiffReview, the other line-comment surface: one row
 * per line, a "+" in the gutter that appears on hover or keyboard focus, and a
 * line's comments shown beneath it rather than behind a toggle.
 *
 * Lines do not wrap by default. Run output is mostly stream-json, where one line
 * is often a few kilobytes; wrapped, the first line alone fills the screen and
 * nothing is scannable. The gutter stays pinned while the text scrolls.
 */

import { useState } from 'react';
import type { KeyboardEvent } from 'react';

import { Button } from './ui/Button';
import { Textarea } from './ui/Textarea';
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
  /** Without it there is nothing to approve with, so no button is drawn. */
  onApprove?: () => void;
  isLoading?: boolean;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
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
  const [composingLine, setComposingLine] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [wrap, setWrap] = useState(false);

  const commentCount = lines.reduce((sum, line) => sum + line.comments.length, 0);

  const closeComposer = () => {
    setComposingLine(null);
    setDraft('');
  };

  const submit = (lineNumber: number) => {
    if (!draft.trim()) return;
    onAddComment?.(lineNumber, draft);
    closeComposer();
  };

  const onComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>, lineNumber: number) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeComposer();
    } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit(lineNumber);
    }
  };

  return (
    <section className="run-output-review" aria-label={`${outputType} review`}>
      <header className="run-output-header">
        <span className={`run-output-kind run-output-kind--${outputType}`}>{outputType}</span>
        <span className="run-output-count">
          {plural(lines.length, 'line')} · {plural(commentCount, 'comment')}
        </span>
        <span className="run-output-header-end">
          {lines.length > 0 ? (
            <Button
              variant="secondary"
              compact
              aria-pressed={wrap}
              className={wrap ? 'active' : undefined}
              onClick={() => setWrap((value) => !value)}
            >
              Wrap lines
            </Button>
          ) : null}
          {approved ? (
            <span className="run-output-approved">Approved by {approvedBy || 'system'}</span>
          ) : onApprove ? (
            <Button variant="primary" compact onClick={onApprove} disabled={isLoading}>
              Approve output
            </Button>
          ) : null}
        </span>
      </header>

      {lines.length === 0 ? (
        <p className="run-output-empty">
          Nothing was written to {outputType}. The other stream may have the run's output.
        </p>
      ) : (
        <div className={`run-output-lines${wrap ? ' run-output-lines--wrap' : ''}`}>
          {lines.map((line) => (
            <div
              key={line.number}
              className={`run-output-row${line.comments.length > 0 ? ' has-comments' : ''}`}
            >
              <div className="run-output-line">
                <span className="run-output-gutter">{line.number}</span>
                {onAddComment ? (
                  <Button
                    variant="plain"
                    className="run-output-add"
                    aria-label={`Comment on line ${line.number}`}
                    title="Comment on this line"
                    disabled={isLoading}
                    onClick={() => {
                      setComposingLine(line.number);
                      setDraft('');
                    }}
                  >
                    +
                  </Button>
                ) : (
                  <span aria-hidden />
                )}
                <code className="run-output-text">{line.content || ' '}</code>
              </div>

              {line.comments.length > 0 ? (
                <div className="run-output-thread">
                  {line.comments.map((comment, idx) => (
                    <div key={`${comment.created_at}-${idx}`} className="run-output-comment">
                      <div className="run-output-comment-meta">
                        <span>{comment.created_by || 'reviewer'}</span>
                        <span>{new Date(comment.created_at).toLocaleString()}</span>
                        {comment.resolved ? (
                          <span className="run-output-resolved">Resolved</span>
                        ) : null}
                      </div>
                      <div className="run-output-comment-body">{comment.content}</div>
                    </div>
                  ))}
                </div>
              ) : null}

              {composingLine === line.number ? (
                <div className="run-output-compose">
                  <Textarea
                    className="run-output-compose-input"
                    aria-label={`Comment on line ${line.number}`}
                    placeholder="Comment on this line… (⌘/Ctrl+Enter to send, Esc to cancel)"
                    rows={3}
                    autoFocus
                    value={draft}
                    disabled={isLoading}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => onComposerKey(event, line.number)}
                  />
                  <div className="run-output-compose-actions">
                    <Button
                      variant="primary"
                      compact
                      disabled={isLoading || !draft.trim()}
                      onClick={() => submit(line.number)}
                    >
                      Comment
                    </Button>
                    <Button variant="secondary" compact disabled={isLoading} onClick={closeComposer}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
