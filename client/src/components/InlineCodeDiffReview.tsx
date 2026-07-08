import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { DiffArtifact, DiffFileSection, DiffLine } from "../api/client";
import {
  addTicketDiffComment,
  diffCommentAnchor,
  listTicketDiffComments,
  submitTicketDiffReviewToAgent,
  type TicketDiffComment,
} from "../lib/diffReviewApi";
import {
  addBranchDiffComment,
  listBranchDiffComments,
  submitBranchDiffReviewToAgent,
  type BranchDiffComment,
} from "../lib/branchTriageApi";
import { TreeExpandChevron } from "./icons/TicketTreeIcons";
import "./InlineCodeDiffReview.css";

type DiffViewMode = "unified" | "split";
type DiffReviewComment = TicketDiffComment | BranchDiffComment;

type LineRef = { line: DiffLine; lineIndex: number };

type SideBySideRow =
  | { kind: "hunk"; line: DiffLine; lineIndex: number }
  | { kind: "change"; left: LineRef | null; right: LineRef | null };

function diffFileSections(diff: DiffArtifact): DiffFileSection[] {
  if (diff.sections?.length) {
    return diff.sections;
  }
  if (!diff.lines?.length) {
    return [];
  }

  const sections: DiffFileSection[] = [];
  let current: DiffFileSection | null = null;

  for (const line of diff.lines) {
    const header = line.text.match(/^\+\+\+ b\/(.+)$/);
    if (header) {
      if (current?.lines.length) {
        sections.push(current);
      }
      current = { path: header[1], add: 0, del: 0, lines: [] };
      continue;
    }
    if (!current) {
      current = { path: diff.file || "changes", add: 0, del: 0, lines: [] };
    }
    current.lines.push(line);
    if (line.type === "a") current.add += 1;
    if (line.type === "d") current.del += 1;
  }
  if (current?.lines.length) {
    sections.push(current);
  }
  return sections;
}

function buildSideBySideRows(lines: DiffLine[]): SideBySideRow[] {
  const rows: SideBySideRow[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (line.type === "h") {
      rows.push({ kind: "hunk", line, lineIndex: index });
      index += 1;
      continue;
    }

    if (line.type === "c") {
      const ref = { line, lineIndex: index };
      rows.push({ kind: "change", left: ref, right: ref });
      index += 1;
      continue;
    }

    if (line.type === "d") {
      const next = lines[index + 1];
      if (next?.type === "a") {
        rows.push({
          kind: "change",
          left: { line, lineIndex: index },
          right: { line: next, lineIndex: index + 1 },
        });
        index += 2;
      } else {
        rows.push({
          kind: "change",
          left: { line, lineIndex: index },
          right: null,
        });
        index += 1;
      }
      continue;
    }

    if (line.type === "a") {
      rows.push({
        kind: "change",
        left: null,
        right: { line, lineIndex: index },
      });
      index += 1;
      continue;
    }

    index += 1;
  }

  return rows;
}

function linePrefix(line: DiffLine): string {
  if (line.type === "a") return "+";
  if (line.type === "d") return "−";
  if (line.type === "h") return "@";
  return " ";
}

function lineClass(line: DiffLine): string {
  if (line.type === "a") return "add";
  if (line.type === "d") return "del";
  if (line.type === "h") return "hunk";
  return "ctx";
}

function ReviewableDiffLine({
  filePath,
  lineRef,
  pane,
  commentsByAnchor,
  activeAnchor,
  draft,
  isLoading,
  onStartComment,
  onDraftChange,
  onCancelComment,
  onSubmitComment,
}: {
  filePath: string;
  lineRef: LineRef | null;
  pane: "old" | "new";
  commentsByAnchor: Map<string, DiffReviewComment[]>;
  activeAnchor: string | null;
  draft: string;
  isLoading: boolean;
  onStartComment: (anchor: string) => void;
  onDraftChange: (value: string) => void;
  onCancelComment: () => void;
  onSubmitComment: (filePath: string, lineIndex: number, lineKind: string) => void;
}) {
  if (!lineRef) {
    return <div className="inline-code-diff-split-empty" aria-hidden="true" />;
  }

  const { line, lineIndex } = lineRef;
  const anchor = diffCommentAnchor(filePath, lineIndex);
  const lineComments = commentsByAnchor.get(anchor) ?? [];
  const isActive = activeAnchor === anchor;
  const canComment = line.type !== "h";
  const showCommentsOnPane =
    pane === "old" ? line.type === "d" || line.type === "c" : line.type === "a" || line.type === "c";

  return (
    <div
      className={`inline-code-diff-line-group ${lineComments.length && showCommentsOnPane ? "has-comments" : ""}`}
    >
      <div className={`inline-code-diff-line ${lineClass(line)}`}>
        <span className="inline-code-diff-gutter">{lineIndex + 1}</span>
        <span className="inline-code-diff-prefix">{linePrefix(line)}</span>
        <code className="inline-code-diff-text">{line.text || " "}</code>
        {canComment && showCommentsOnPane ? (
          <button
            type="button"
            className="inline-code-diff-add-btn"
            title="Add inline review comment"
            disabled={isLoading}
            onClick={() => onStartComment(anchor)}
          >
            +
          </button>
        ) : null}
        {lineComments.length > 0 && showCommentsOnPane ? (
          <span className="inline-code-diff-comment-badge" title="Comments on this line">
            💬 {lineComments.length}
          </span>
        ) : null}
      </div>

      {showCommentsOnPane && lineComments.length > 0 ? (
        <div className="inline-code-diff-thread">
          {lineComments.map((comment) => (
            <div key={comment.id} className="inline-code-diff-comment">
              <div className="inline-code-diff-comment-meta">
                <span>{comment.created_by || "reviewer"}</span>
                <span>{new Date(comment.created_at).toLocaleString()}</span>
              </div>
              <div className="inline-code-diff-comment-body">{comment.content}</div>
            </div>
          ))}
        </div>
      ) : null}

      {isActive && showCommentsOnPane ? (
        <div className="inline-code-diff-compose">
          <textarea
            className="inline-code-diff-compose-input"
            rows={3}
            autoFocus
            placeholder="Leave an inline code review comment…"
            value={draft}
            disabled={isLoading}
            onChange={(e) => onDraftChange(e.target.value)}
          />
          <div className="inline-code-diff-compose-actions">
            <button
              type="button"
              className="btn-primary btn-compact"
              disabled={isLoading || !draft.trim()}
              onClick={() => onSubmitComment(filePath, lineIndex, line.type)}
            >
              Comment
            </button>
            <button
              type="button"
              className="btn-secondary btn-compact"
              disabled={isLoading}
              onClick={onCancelComment}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function UnifiedDiffFile({
  section,
  commentsByAnchor,
  activeAnchor,
  draft,
  isLoading,
  onStartComment,
  onDraftChange,
  onCancelComment,
  onSubmitComment,
}: {
  section: DiffFileSection;
  commentsByAnchor: Map<string, DiffReviewComment[]>;
  activeAnchor: string | null;
  draft: string;
  isLoading: boolean;
  onStartComment: (anchor: string) => void;
  onDraftChange: (value: string) => void;
  onCancelComment: () => void;
  onSubmitComment: (filePath: string, lineIndex: number, lineKind: string) => void;
}) {
  return (
    <div className="inline-code-diff-file">
      {section.lines.map((line, lineIndex) => {
        const anchor = diffCommentAnchor(section.path, lineIndex);
        const lineComments = commentsByAnchor.get(anchor) ?? [];
        const isActive = activeAnchor === anchor;
        const canComment = line.type !== "h";

        return (
          <div
            key={`${section.path}-${lineIndex}`}
            className={`inline-code-diff-line-group ${lineComments.length ? "has-comments" : ""}`}
          >
            <div className={`inline-code-diff-line ${lineClass(line)}`}>
              <span className="inline-code-diff-gutter">{lineIndex + 1}</span>
              <span className="inline-code-diff-prefix">{linePrefix(line)}</span>
              <code className="inline-code-diff-text">{line.text || " "}</code>
              {canComment ? (
                <button
                  type="button"
                  className="inline-code-diff-add-btn"
                  title="Add inline review comment"
                  disabled={isLoading}
                  onClick={() => onStartComment(anchor)}
                >
                  +
                </button>
              ) : null}
              {lineComments.length > 0 ? (
                <span className="inline-code-diff-comment-badge" title="Comments on this line">
                  💬 {lineComments.length}
                </span>
              ) : null}
            </div>

            {lineComments.length > 0 ? (
              <div className="inline-code-diff-thread">
                {lineComments.map((comment) => (
                  <div key={comment.id} className="inline-code-diff-comment">
                    <div className="inline-code-diff-comment-meta">
                      <span>{comment.created_by || "reviewer"}</span>
                      <span>{new Date(comment.created_at).toLocaleString()}</span>
                    </div>
                    <div className="inline-code-diff-comment-body">{comment.content}</div>
                  </div>
                ))}
              </div>
            ) : null}

            {isActive ? (
              <div className="inline-code-diff-compose">
                <textarea
                  className="inline-code-diff-compose-input"
                  rows={3}
                  autoFocus
                  placeholder="Leave an inline code review comment…"
                  value={draft}
                  disabled={isLoading}
                  onChange={(e) => onDraftChange(e.target.value)}
                />
                <div className="inline-code-diff-compose-actions">
                  <button
                    type="button"
                    className="btn-primary btn-compact"
                    disabled={isLoading || !draft.trim()}
                    onClick={() => onSubmitComment(section.path, lineIndex, line.type)}
                  >
                    Comment
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={isLoading}
                    onClick={onCancelComment}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function SplitDiffFile({
  section,
  commentsByAnchor,
  activeAnchor,
  draft,
  isLoading,
  onStartComment,
  onDraftChange,
  onCancelComment,
  onSubmitComment,
}: {
  section: DiffFileSection;
  commentsByAnchor: Map<string, DiffReviewComment[]>;
  activeAnchor: string | null;
  draft: string;
  isLoading: boolean;
  onStartComment: (anchor: string) => void;
  onDraftChange: (value: string) => void;
  onCancelComment: () => void;
  onSubmitComment: (filePath: string, lineIndex: number, lineKind: string) => void;
}) {
  const rows = useMemo(() => buildSideBySideRows(section.lines), [section.lines]);

  return (
    <div className="inline-code-diff-split">
      <div className="inline-code-diff-split-header">
        <span>Before</span>
        <span>After</span>
      </div>
      {rows.map((row, rowIndex) => {
        if (row.kind === "hunk") {
          return (
            <div
              key={`${section.path}-hunk-${row.lineIndex}`}
              className="inline-code-diff-split-hunk"
            >
              <code>{row.line.text}</code>
            </div>
          );
        }

        return (
          <div key={`${section.path}-split-${rowIndex}`} className="inline-code-diff-split-row">
            <div className="inline-code-diff-split-pane inline-code-diff-split-pane--old">
              <ReviewableDiffLine
                filePath={section.path}
                lineRef={row.left}
                pane="old"
                commentsByAnchor={commentsByAnchor}
                activeAnchor={activeAnchor}
                draft={draft}
                isLoading={isLoading}
                onStartComment={onStartComment}
                onDraftChange={onDraftChange}
                onCancelComment={onCancelComment}
                onSubmitComment={onSubmitComment}
              />
            </div>
            <div className="inline-code-diff-split-pane inline-code-diff-split-pane--new">
              <ReviewableDiffLine
                filePath={section.path}
                lineRef={row.right}
                pane="new"
                commentsByAnchor={commentsByAnchor}
                activeAnchor={activeAnchor}
                draft={draft}
                isLoading={isLoading}
                onStartComment={onStartComment}
                onDraftChange={onDraftChange}
                onCancelComment={onCancelComment}
                onSubmitComment={onSubmitComment}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function InlineCodeDiffReview({
  ticketId,
  branchReview,
  diff,
  diffSummary,
  onOpenEditorFile,
  lazyLoadFiles = false,
  onLoadFile,
}: {
  ticketId?: string;
  branchReview?: { workspaceSlug: string; branch: string };
  diff: DiffArtifact;
  diffSummary?: {
    files: string;
    range?: string;
    add: string;
    del: string;
  };
  onOpenEditorFile?: (filePath: string) => void;
  lazyLoadFiles?: boolean;
  onLoadFile?: (filePath: string) => Promise<DiffFileSection | null>;
}) {
  const sections = useMemo(() => diffFileSections(diff), [diff]);
  const fileRows = useMemo(
    () =>
      diff.file_entries?.length
        ? diff.file_entries
        : sections.map((section) => ({
            path: section.path,
            add: section.add,
            del: section.del,
          })),
    [diff.file_entries, sections],
  );
  const sectionPathsKey = useMemo(() => fileRows.map((row) => row.path).join("\0"), [fileRows]);
  const queryClient = useQueryClient();
  const [viewMode, setViewMode] = useState<DiffViewMode>("unified");
  const [collapsedFiles, setCollapsedFiles] = useState<Set<string>>(() => new Set());
  const [expandedFile, setExpandedFile] = useState<string | null>(null);
  const [loadedSections, setLoadedSections] = useState<Map<string, DiffFileSection>>(() => new Map());
  const [loadingFile, setLoadingFile] = useState<string | null>(null);
  const [comments, setComments] = useState<DiffReviewComment[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeAnchor, setActiveAnchor] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [submitInstructions, setSubmitInstructions] = useState("");
  const [showSubmit, setShowSubmit] = useState(false);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);

  const commentsByAnchor = useMemo(() => {
    const map = new Map<string, DiffReviewComment[]>();
    for (const comment of comments) {
      const key = diffCommentAnchor(comment.file_path, comment.line_index);
      const bucket = map.get(key) ?? [];
      bucket.push(comment);
      map.set(key, bucket);
    }
    return map;
  }, [comments]);

  const unresolvedCount = useMemo(
    () => comments.filter((comment) => !comment.resolved).length,
    [comments],
  );

  const branchWorkspaceSlug = branchReview?.workspaceSlug;
  const branchName = branchReview?.branch;

  useEffect(() => {
    setCollapsedFiles(new Set());
    setExpandedFile(null);
    setLoadedSections(new Map());
    setLoadingFile(null);
  }, [sectionPathsKey]);

  const toggleFileCollapsed = (path: string) => {
    if (lazyLoadFiles && onLoadFile) {
      if (expandedFile === path) {
        setExpandedFile(null);
        setLoadedSections(new Map());
        return;
      }
      setExpandedFile(path);
      setLoadedSections(new Map());
      setLoadingFile(path);
      void onLoadFile(path)
        .then((section) => {
          if (section) {
            setLoadedSections(new Map([[path, section]]));
          }
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : "Failed to load file diff");
        })
        .finally(() => {
          setLoadingFile((current) => (current === path ? null : current));
        });
      return;
    }

    setCollapsedFiles((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const collapseAllFiles = () => {
    if (lazyLoadFiles) {
      setExpandedFile(null);
      setLoadedSections(new Map());
      return;
    }
    setCollapsedFiles(new Set(fileRows.map((row) => row.path)));
  };

  const expandAllFiles = () => {
    if (lazyLoadFiles) {
      return;
    }
    setCollapsedFiles(new Set());
  };

  const collapsedCount = lazyLoadFiles
    ? fileRows.length - (expandedFile ? 1 : 0)
    : collapsedFiles.size;

  const resolveSection = (path: string): DiffFileSection | null => {
    if (lazyLoadFiles) {
      if (expandedFile !== path) return null;
      return loadedSections.get(path) ?? null;
    }
    if (collapsedFiles.has(path)) return null;
    return sections.find((section) => section.path === path) ?? null;
  };

  const isFileCollapsed = (path: string) => {
    if (lazyLoadFiles) return expandedFile !== path;
    return collapsedFiles.has(path);
  };

  const refreshComments = useCallback(async () => {
    setError(null);
    if (branchWorkspaceSlug && branchName) {
      const data = await listBranchDiffComments(branchWorkspaceSlug, branchName);
      setComments(data.comments ?? []);
      return;
    }
    if (!ticketId) {
      setComments([]);
      return;
    }
    const data = await listTicketDiffComments(ticketId);
    setComments(data.comments ?? []);
  }, [branchWorkspaceSlug, branchName, ticketId]);

  useEffect(() => {
    if (sections.length === 0 && ticketId) {
      void queryClient.invalidateQueries({ queryKey: ["ticket", ticketId] });
    }
  }, [sections.length, queryClient, ticketId]);

  useEffect(() => {
    void refreshComments().catch((err) => {
      setError(err instanceof Error ? err.message : "Failed to load review comments");
    });
  }, [refreshComments]);

  const handleAddComment = async (
    filePath: string,
    lineIndex: number,
    lineKind: string,
  ) => {
    if (!draft.trim()) return;
    setIsLoading(true);
    setError(null);
    try {
      if (branchWorkspaceSlug && branchName) {
        await addBranchDiffComment(branchWorkspaceSlug, branchName, {
          file_path: filePath,
          line_index: lineIndex,
          line_kind: lineKind,
          content: draft.trim(),
        });
      } else if (ticketId) {
        await addTicketDiffComment(ticketId, {
          file_path: filePath,
          line_index: lineIndex,
          line_kind: lineKind,
          content: draft.trim(),
        });
      }
      setDraft("");
      setActiveAnchor(null);
      await refreshComments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add comment");
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmitToAgent = async () => {
    setIsLoading(true);
    setError(null);
    setSubmitMessage(null);
    try {
      const result =
        branchWorkspaceSlug && branchName
          ? await submitBranchDiffReviewToAgent(branchWorkspaceSlug, branchName, {
              instructions: submitInstructions,
            })
          : ticketId
            ? await submitTicketDiffReviewToAgent(ticketId, {
                instructions: submitInstructions,
              })
            : { submitted_comments: 0 };
      setSubmitMessage(
        `Submitted ${result.submitted_comments} inline comment${
          result.submitted_comments === 1 ? "" : "s"
        } to the agent via triage.`,
      );
      setShowSubmit(false);
      setSubmitInstructions("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit review");
    } finally {
      setIsLoading(false);
    }
  };

  const startComment = (anchor: string) => {
    setActiveAnchor(anchor);
    setDraft("");
  };

  const sharedFileProps = {
    commentsByAnchor,
    activeAnchor,
    draft,
    isLoading,
    onStartComment: startComment,
    onDraftChange: setDraft,
    onCancelComment: () => {
      setActiveAnchor(null);
      setDraft("");
    },
    onSubmitComment: (filePath: string, lineIndex: number, lineKind: string) => {
      void handleAddComment(filePath, lineIndex, lineKind);
    },
  };

  return (
    <div className="inline-code-diff-review">
      <div className="inline-code-diff-toolbar">
        {diffSummary ? (
          <div className="inline-code-diff-summary">
            <span className="inline-code-diff-summary-files" title={diffSummary.files}>
              {diffSummary.files}
            </span>
            {diffSummary.range ? (
              <span className="inline-code-diff-summary-range">vs {diffSummary.range}</span>
            ) : null}
            <span className="inline-code-diff-summary-add">{diffSummary.add}</span>
            <span className="inline-code-diff-summary-del">{diffSummary.del}</span>
          </div>
        ) : null}
        <div className="inline-code-diff-toolbar-row">
          <div className="inline-code-diff-hint">
            Hover a line and click <strong>+</strong> to comment.
          </div>
          <div className="inline-code-diff-actions">
          <div className="inline-code-diff-view-toggle">
            <button
              type="button"
              className={`btn-secondary btn-compact ${viewMode === "split" ? "active" : ""}`}
              onClick={() => setViewMode("split")}
            >
              Split
            </button>
            <button
              type="button"
              className={`btn-secondary btn-compact ${viewMode === "unified" ? "active" : ""}`}
              onClick={() => setViewMode("unified")}
            >
              Unified
            </button>
          </div>
          {fileRows.length > 1 ? (
            <div className="inline-code-diff-file-toggle">
              <button
                type="button"
                className="btn-secondary btn-compact"
                disabled={lazyLoadFiles ? !expandedFile : collapsedCount === fileRows.length}
                onClick={collapseAllFiles}
              >
                Collapse all
              </button>
              {!lazyLoadFiles ? (
                <button
                  type="button"
                  className="btn-secondary btn-compact"
                  disabled={collapsedCount === 0}
                  onClick={expandAllFiles}
                >
                  Expand all
                </button>
              ) : null}
            </div>
          ) : null}
          <span className="inline-code-diff-count">
            {unresolvedCount} open comment{unresolvedCount === 1 ? "" : "s"}
          </span>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={isLoading || unresolvedCount === 0}
            onClick={() => setShowSubmit((open) => !open)}
          >
            Submit review to agent
          </button>
        </div>
        </div>
      </div>

      {error ? <div className="inline-code-diff-error">{error}</div> : null}
      {submitMessage ? <div className="inline-code-diff-success">{submitMessage}</div> : null}

      {showSubmit ? (
        <div className="inline-code-diff-submit">
          <textarea
            className="inline-code-diff-submit-input"
            rows={3}
            placeholder="Optional instructions for the agent alongside your inline comments…"
            value={submitInstructions}
            disabled={isLoading}
            onChange={(e) => setSubmitInstructions(e.target.value)}
          />
          <div className="inline-code-diff-submit-actions">
            <button
              type="button"
              className="btn-primary btn-compact"
              disabled={isLoading}
              onClick={() => void handleSubmitToAgent()}
            >
              Send to agent
            </button>
            <button
              type="button"
              className="btn-secondary btn-compact"
              disabled={isLoading}
              onClick={() => setShowSubmit(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {fileRows.length === 0 ? (
        <div className="inline-code-diff-empty">
          Diff summary is available but line content did not load. Re-open this tab or run the ticket again to refresh
          the git diff.
        </div>
      ) : null}

      {lazyLoadFiles ? (
        <div className="inline-code-diff-lazy-hint">
          Expand a file to load its diff. Only one file is kept loaded at a time.
        </div>
      ) : null}

      {fileRows.map((row) => {
        const isCollapsed = isFileCollapsed(row.path);
        const section = resolveSection(row.path);
        const isLoadingFile = loadingFile === row.path;
        return (
        <section
          key={row.path}
          className={`diff-file-block${isCollapsed ? " diff-file-block--collapsed" : ""}`}
        >
          <div className="diff-file-header">
            <button
              type="button"
              className="diff-file-collapse-btn"
              aria-expanded={!isCollapsed}
              aria-label={isCollapsed ? `Expand ${row.path}` : `Collapse ${row.path}`}
              onClick={() => toggleFileCollapsed(row.path)}
            >
              <TreeExpandChevron expanded={!isCollapsed} />
            </button>
            {onOpenEditorFile ? (
              <button
                type="button"
                className="diff-file-path diff-file-open-btn"
                title={`Open ${row.path} in editor`}
                onClick={() => onOpenEditorFile(row.path)}
              >
                {row.path}
              </button>
            ) : (
              <button
                type="button"
                className="diff-file-path diff-file-path-toggle"
                title={row.path}
                onClick={() => toggleFileCollapsed(row.path)}
              >
                {row.path}
              </button>
            )}
            <span className="diff-file-stats">
              <span style={{ color: "var(--grl)" }}>+{row.add}</span>
              <span style={{ color: "var(--rdl)" }}>−{row.del}</span>
              {section?.truncated ? (
                <span className="diff-file-truncated-pill" title="Line view truncated; counts are from git">
                  truncated view
                </span>
              ) : null}
            </span>
          </div>

          {isLoadingFile ? (
            <div className="branch-triage-empty diff-file-loading">Loading {row.path}…</div>
          ) : null}

          {!isCollapsed && section && !isLoadingFile ? (
            viewMode === "split" ? (
              <SplitDiffFile section={section} {...sharedFileProps} />
            ) : (
              <UnifiedDiffFile section={section} {...sharedFileProps} />
            )
          ) : null}
        </section>
        );
      })}
    </div>
  );
}
