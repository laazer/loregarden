import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { CodeEditor } from "../components/editor/CodeEditor";
import { EditorFileTree } from "../components/editor/EditorFileTree";
import { EditorStartPane } from "../components/editor/EditorStartPane";
import { GitRefSwitcher } from "../components/editor/GitRefSwitcher";
import { PageTopbar } from "../components/TopbarPageSlot";
import { Button } from "../components/ui/Button";
import { PaneSkeleton } from "../components/ui/PaneSkeleton";
import { describeError } from "../state/toastStore";
import { editorRecentKey, useUiStore } from "../state/uiStore";
import { Select } from "../components/ui/Select";

export function EditorPage() {
  const qc = useQueryClient();
  const editorWorkspace = useUiStore((s) => s.editorWorkspace);
  const editorContextRoot = useUiStore((s) => s.editorContextRoot);
  const editorFilePath = useUiStore((s) => s.editorFilePath);
  const setEditorWorkspace = useUiStore((s) => s.setEditorWorkspace);
  const setEditorContextRoot = useUiStore((s) => s.setEditorContextRoot);
  const setEditorFilePath = useUiStore((s) => s.setEditorFilePath);
  const rememberEditorFile = useUiStore((s) => s.rememberEditorFile);
  const editorRecentFiles = useUiStore((s) => s.editorRecentFiles);

  const [draftContent, setDraftContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const workspaceSlug = editorWorkspace || workspaces.data?.[0]?.slug || "";

  const refs = useQuery({
    queryKey: ["editor-refs", workspaceSlug, editorContextRoot],
    queryFn: () => api.editorRefs(workspaceSlug, editorContextRoot || undefined),
    enabled: Boolean(workspaceSlug),
  });

  const contextRoot = refs.data?.context_root ?? editorContextRoot ?? ".";

  const activeWorkspace = workspaces.data?.find((ws) => ws.slug === workspaceSlug);
  const repoReady = Boolean(workspaceSlug && activeWorkspace?.repo_exists);

  const changesQuery = useQuery({
    queryKey: ["editor-changes", workspaceSlug, contextRoot],
    queryFn: () => api.editorChanges(workspaceSlug, contextRoot || undefined),
    enabled: repoReady,
  });
  const changedPaths = useMemo(
    () => new Map((changesQuery.data?.files ?? []).map((change) => [change.path, change.status])),
    [changesQuery.data],
  );
  const recentKey = editorRecentKey(workspaceSlug, contextRoot);
  const recentFiles = editorRecentFiles[recentKey] ?? [];

  const fileQuery = useQuery({
    queryKey: ["editor-file", workspaceSlug, contextRoot, editorFilePath],
    queryFn: () => api.editorReadFile(workspaceSlug, editorFilePath!, contextRoot || undefined),
    enabled: Boolean(workspaceSlug && editorFilePath),
  });

  useEffect(() => {
    if (!workspaceSlug && workspaces.data?.length) {
      setEditorWorkspace(workspaces.data[0].slug);
    }
  }, [workspaceSlug, workspaces.data, setEditorWorkspace]);

  useEffect(() => {
    if (refs.data?.context_root && refs.data.context_root !== editorContextRoot) {
      setEditorContextRoot(refs.data.context_root);
    }
  }, [refs.data?.context_root, editorContextRoot, setEditorContextRoot]);

  useEffect(() => {
    if (fileQuery.data) {
      setDraftContent(fileQuery.data.content);
      setSavedContent(fileQuery.data.content);
      // Remembered once it has opened, so a path that no longer exists never
      // lands in Recent.
      rememberEditorFile(recentKey, fileQuery.data.path);
    }
  }, [fileQuery.data, recentKey, rememberEditorFile]);

  useEffect(() => {
    setDraftContent("");
    setSavedContent("");
    setSaveMessage(null);
  }, [editorFilePath, contextRoot, workspaceSlug]);

  const saveFile = useMutation({
    meta: { errorTitle: "Save file" },
    mutationFn: () =>
      api.editorWriteFile(workspaceSlug, {
        path: editorFilePath!,
        content: draftContent,
        context_root: contextRoot || undefined,
      }),
    onSuccess: () => {
      setSavedContent(draftContent);
      setSaveMessage("Saved");
      qc.invalidateQueries({ queryKey: ["editor-file", workspaceSlug, contextRoot, editorFilePath] });
      qc.invalidateQueries({ queryKey: ["editor-changes", workspaceSlug, contextRoot] });
      window.setTimeout(() => setSaveMessage(null), 2000);
    },
    onError: (error) => {
      setSaveMessage(describeError(error, "Save failed"));
    },
  });

  const dirty = useMemo(
    () => Boolean(editorFilePath) && draftContent !== savedContent,
    [draftContent, savedContent, editorFilePath],
  );

  // Switching files drops the draft, so unsaved edits are only discarded when
  // the operator says so.
  const leaveFile = (next: string | null) => {
    if (next === editorFilePath) return;
    if (dirty && !window.confirm(`Discard unsaved changes to ${editorFilePath}?`)) return;
    setEditorFilePath(next);
  };

  return (
    <div className="screen-view">
      <PageTopbar title="Editor">
        <label className="topbar-workspace-picker">
          <span className="topbar-workspace-picker-label">Workspace</span>
          <Select
            className="btn-secondary topbar-workspace-picker-select"
            value={workspaceSlug}
            disabled={!workspaces.data?.length}
            aria-label="Editor workspace"
            onChange={(event) => {
              setEditorWorkspace(event.target.value);
              setEditorContextRoot(".");
              setEditorFilePath(null);
            }}
          >
            {(workspaces.data ?? []).map((ws) => (
              <option key={ws.slug} value={ws.slug}>
                {ws.name}
              </option>
            ))}
          </Select>
        </label>

        <GitRefSwitcher
          workspaceSlug={workspaceSlug}
          refs={refs.data}
          contextRoot={contextRoot}
          isLoading={refs.isLoading}
          disabled={!workspaceSlug || !activeWorkspace?.repo_exists}
        />

        {saveMessage ? <span className="editor-save-status">{saveMessage}</span> : null}
      </PageTopbar>

      <div className="editor-layout">
        <aside className="editor-sidebar">
          <div className="editor-sidebar-title">Explorer</div>
          {!activeWorkspace?.repo_exists ? (
            <p className="modal-hint editor-sidebar-empty">
              Workspace repo is missing. Check the repo path in workspace settings.
            </p>
          ) : (
            <EditorFileTree
              key={`${workspaceSlug}::${contextRoot}`}
              workspaceSlug={workspaceSlug}
              contextRoot={contextRoot}
              selectedPath={editorFilePath}
              onOpenFile={leaveFile}
              changes={changedPaths}
            />
          )}
        </aside>

        <main className="editor-main">
          {editorFilePath ? (
            fileQuery.isLoading ? (
              <PaneSkeleton variant="code" label={`Loading ${editorFilePath}…`} />
            ) : fileQuery.error ? (
              <div className="editor-empty">
                <p className="editor-empty-error">{describeError(fileQuery.error, `Couldn't open ${editorFilePath}`)}</p>
                <Button variant="secondary" compact onClick={() => setEditorFilePath(null)}>
                  Back to changed and recent files
                </Button>
              </div>
            ) : (
              <CodeEditor
                path={editorFilePath}
                language={fileQuery.data?.language ?? "plaintext"}
                value={draftContent}
                dirty={dirty}
                onChange={setDraftContent}
                onClose={() => leaveFile(null)}
                onSave={() => {
                  if (!dirty || saveFile.isPending) return;
                  saveFile.mutate();
                }}
              />
            )
          ) : activeWorkspace?.repo_exists ? (
            <EditorStartPane
              changes={changesQuery.data}
              changesLoading={changesQuery.isLoading}
              changesError={changesQuery.error}
              recent={recentFiles}
              onOpenFile={leaveFile}
            />
          ) : (
            <div className="editor-empty">
              <div className="editor-empty-title">No repository to edit</div>
              <p className="modal-hint">Pick a workspace whose repo exists, or fix its repo path in workspace settings.</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
