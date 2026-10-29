import { useId, useState } from "react";

import type { EditorChange, EditorChangesResponse } from "../../api/types";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { CHANGE_MARK } from "./editorChangeMarks";

import "./EditorStartPane.css";

/** Past this many changed files, a filter sits above the list. */
const FILTER_THRESHOLD = 50;

function splitPath(path: string): { name: string; folder: string } {
  const at = path.lastIndexOf("/");
  return at < 0 ? { name: path, folder: "" } : { name: path.slice(at + 1), folder: path.slice(0, at) };
}

function ChangeCounts({ change }: { change: EditorChange }) {
  if (change.additions === null || change.deletions === null) return null;
  return (
    <span className="editor-start-counts">
      <span className="is-add">+{change.additions}</span> <span className="is-del">−{change.deletions}</span>
    </span>
  );
}

function ChangeRow({ change, onOpenFile }: { change: EditorChange; onOpenFile: (path: string) => void }) {
  const { name, folder } = splitPath(change.path);
  const mark = CHANGE_MARK[change.status];
  const body = (
    <>
      <span className={`editor-start-mark is-${change.status}`} title={mark.label}>
        {mark.letter}
      </span>
      <span className="editor-start-name">{name}</span>
      {folder ? <span className="editor-start-folder">{folder}</span> : null}
      <ChangeCounts change={change} />
    </>
  );
  // A deleted file has nothing to open; it stays in the list so the count is
  // honest, and says why it is not a link.
  if (change.status === "deleted") {
    return (
      <li className="editor-start-row is-deleted" title={`${change.path} was deleted on this branch`}>
        {body}
      </li>
    );
  }
  return (
    <li>
      <Button variant="plain" className="editor-start-row" title={`Open ${change.path}`} onClick={() => onOpenFile(change.path)}>
        {body}
      </Button>
    </li>
  );
}

function ChangedFiles({
  changes,
  loading,
  error,
  onOpenFile,
}: {
  changes: EditorChangesResponse | undefined;
  loading: boolean;
  error: unknown;
  onOpenFile: (path: string) => void;
}) {
  const headingId = useId();
  const [filter, setFilter] = useState("");

  let body;
  if (loading) {
    body = <PaneSkeleton variant="list" label="Reading what this branch changed…" />;
  } else if (error) {
    body = <p className="editor-start-error">{describeError(error, "Couldn't read what this branch changed")}</p>;
  } else if (!changes || changes.files.length === 0) {
    body = (
      <p className="editor-start-hint">
        Nothing changed on {changes?.branch ?? "this branch"} since it left {changes?.base ?? "its base"}. Files you
        edit here, saved or not yet committed, show up in this list.
      </p>
    );
  } else {
    const needle = filter.trim().toLowerCase();
    const shown = needle ? changes.files.filter((change) => change.path.toLowerCase().includes(needle)) : changes.files;
    body = (
      <>
        {changes.files.length > FILTER_THRESHOLD ? (
          <Input
            className="editor-start-filter"
            aria-label="Filter changed files"
            placeholder="Filter by path"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        ) : null}
        {shown.length === 0 ? (
          <p className="editor-start-hint">No changed file matches “{filter}”.</p>
        ) : (
          <ul className="editor-start-list">
            {shown.map((change) => (
              <ChangeRow key={change.path} change={change} onOpenFile={onOpenFile} />
            ))}
          </ul>
        )}
        {changes.total > changes.files.length ? (
          <p className="editor-start-hint">
            And {changes.total - changes.files.length} more. A branch this large reads better as a pull request.
          </p>
        ) : null}
      </>
    );
  }

  return (
    <section className="editor-start-section" aria-labelledby={headingId}>
      <h2 id={headingId} className="editor-start-heading">
        Changed on <span className="editor-start-ref">{changes?.branch ?? "this branch"}</span>
        {changes && changes.total > 0 ? <span className="editor-start-total">{changes.total}</span> : null}
      </h2>
      {changes ? (
        <p className="editor-start-meta">
          Since it left <span className="editor-start-ref">{changes.base}</span> at{" "}
          <span className="editor-start-ref">{changes.merge_base}</span>, including edits not yet committed
        </p>
      ) : null}
      {body}
    </section>
  );
}

function RecentFiles({ recent, onOpenFile }: { recent: string[]; onOpenFile: (path: string) => void }) {
  const headingId = useId();
  return (
    <section className="editor-start-section" aria-labelledby={headingId}>
      <h2 id={headingId} className="editor-start-heading">
        Recent
      </h2>
      {recent.length === 0 ? (
        <p className="editor-start-hint">Files you open here appear in this list, newest first.</p>
      ) : (
        <ul className="editor-start-list">
          {recent.map((path) => {
            const { name, folder } = splitPath(path);
            return (
              <li key={path}>
                <Button variant="plain" className="editor-start-row" title={`Open ${path}`} onClick={() => onOpenFile(path)}>
                  <span className="editor-start-name">{name}</span>
                  {folder ? <span className="editor-start-folder">{folder}</span> : null}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/**
 * What the Editor shows with no file open: what this branch changed, and what
 * you opened last. Both are a click from being edited.
 *
 * Answers "where was I, and what has this branch touched?" before the operator
 * has to go looking in the tree.
 */
export function EditorStartPane({
  changes,
  changesLoading,
  changesError,
  recent,
  onOpenFile,
}: {
  changes: EditorChangesResponse | undefined;
  changesLoading: boolean;
  changesError: unknown;
  recent: string[];
  onOpenFile: (path: string) => void;
}) {
  return (
    <div className="editor-start">
      <ChangedFiles changes={changes} loading={changesLoading} error={changesError} onOpenFile={onOpenFile} />
      <RecentFiles recent={recent} onOpenFile={onOpenFile} />
    </div>
  );
}
