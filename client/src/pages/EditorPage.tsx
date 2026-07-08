import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { PageHeroAppToolbar } from "../components/PageHeroAppToolbar";
import { CodeEditor } from "../components/editor/CodeEditor";
import { EditorFileExplorer } from "../components/editor/EditorFileExplorer";
import { GitRefSwitcher } from "../components/editor/GitRefSwitcher";
import { useUiStore } from "../state/uiStore";

export function EditorPage() {
  const qc = useQueryClient();
  const editorWorkspace = useUiStore((s) => s.editorWorkspace);
  const editorContextRoot = useUiStore((s) => s.editorContextRoot);
  const editorFilePath = useUiStore((s) => s.editorFilePath);
  const setEditorWorkspace = useUiStore((s) => s.setEditorWorkspace);
  const setEditorContextRoot = useUiStore((s) => s.setEditorContextRoot);
  const setEditorFilePath = useUiStore((s) => s.setEditorFilePath);

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
    }
  }, [fileQuery.data]);

  useEffect(() => {
    setDraftContent("");
    setSavedContent("");
    setSaveMessage(null);
  }, [editorFilePath, contextRoot, workspaceSlug]);

  const saveFile = useMutation({
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
      window.setTimeout(() => setSaveMessage(null), 2000);
    },
    onError: (error) => {
      setSaveMessage(error instanceof Error ? error.message : "Save failed");
    },
  });

  const dirty = useMemo(
    () => Boolean(editorFilePath) && draftContent !== savedContent,
    [draftContent, savedContent, editorFilePath],
  );

  const activeWorkspace = workspaces.data?.find((ws) => ws.slug === workspaceSlug);

  return (
    <div className="screen-view screen-view--editor editor-page">
      <header className="page-hero-header editor-topbar">
        <div className="page-hero-copy">
          <div className="page-hero-eyebrow">
            <span>Console</span>
            <span className="page-hero-eyebrow-dot" aria-hidden />
            <span className="page-hero-eyebrow-muted">File Editor</span>
          </div>
          <h1 className="page-hero-title">File Editor</h1>
          <p className="page-hero-sub">Browse, view, and edit workspace files</p>
        </div>

        <div className="page-hero-actions">
          <label className="editor-workspace-picker">
            <span className="page-hero-field-label">Workspace</span>
            <select
              className="btn-secondary page-hero-field-select"
              value={workspaceSlug}
              disabled={!workspaces.data?.length}
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
            </select>
          </label>

          <GitRefSwitcher
            workspaceSlug={workspaceSlug}
            refs={refs.data}
            contextRoot={contextRoot}
            isLoading={refs.isLoading}
            disabled={!workspaceSlug || !activeWorkspace?.repo_exists}
          />

          {saveMessage ? <span className="editor-save-status">{saveMessage}</span> : null}
          <PageHeroAppToolbar />
        </div>
      </header>

      <div className="editor-layout">
        <aside className="editor-sidebar">
          <div className="editor-sidebar-title">Explorer</div>
          {!activeWorkspace?.repo_exists ? (
            <p className="modal-hint editor-sidebar-empty">
              Workspace repo is missing. Check the repo path in workspace settings.
            </p>
          ) : (
            <EditorFileExplorer
              workspaceSlug={workspaceSlug}
              contextRoot={contextRoot}
              selectedPath={editorFilePath}
              onOpenFile={setEditorFilePath}
              disabled={!workspaceSlug}
            />
          )}
        </aside>

        <main className="editor-main">
          {editorFilePath ? (
            fileQuery.isLoading ? (
              <div className="editor-empty">Loading file…</div>
            ) : fileQuery.error ? (
              <div className="editor-empty editor-empty-error">
                {fileQuery.error instanceof Error ? fileQuery.error.message : "Failed to load file"}
              </div>
            ) : (
              <CodeEditor
                path={editorFilePath}
                language={fileQuery.data?.language ?? "plaintext"}
                value={draftContent}
                dirty={dirty}
                onChange={setDraftContent}
                onSave={() => {
                  if (!dirty || saveFile.isPending) return;
                  saveFile.mutate();
                }}
              />
            )
          ) : (
            <div className="editor-empty">
              <div className="editor-empty-title">Select a file</div>
              <p className="modal-hint">
                Open a text file from the explorer, or switch branches and worktrees from the header.
              </p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
