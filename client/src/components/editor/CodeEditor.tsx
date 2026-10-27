import Editor, { type OnMount } from "@monaco-editor/react";
import { useCallback } from "react";

import { Button } from "../ui/Button";

interface CodeEditorProps {
  path: string;
  language: string;
  value: string;
  dirty: boolean;
  readOnly?: boolean;
  onChange: (value: string) => void;
  onSave: () => void;
  /** Back to the start pane; the page asks before discarding unsaved edits. */
  onClose: () => void;
}

export function CodeEditor({
  path,
  language,
  value,
  dirty,
  readOnly = false,
  onChange,
  onSave,
  onClose,
}: CodeEditorProps) {
  const handleMount: OnMount = useCallback(
    (editor, monaco) => {
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        onSave();
      });
      editor.focus();
    },
    [onSave],
  );

  return (
    <div className="code-editor-shell">
      <div className="code-editor-tabbar">
        <span className="code-editor-tab active">
          {path}
          {dirty ? <span className="code-editor-dirty">●</span> : null}
        </span>
        <Button
          variant="plain"
          className="code-editor-close"
          aria-label={`Close ${path}`}
          title="Close this file"
          onClick={onClose}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </Button>
        <div style={{ flex: 1 }} />
        <button
          type="button"
          className="btn-primary btn-compact"
          disabled={!dirty || readOnly}
          onClick={onSave}
        >
          Save
        </button>
      </div>
      <div className="code-editor-body">
        <Editor
          height="100%"
          language={language}
          value={value}
          theme="vs-dark"
          options={{
            readOnly,
            minimap: { enabled: false },
            fontFamily: "JetBrains Mono, ui-monospace, monospace",
            fontSize: 13, // type-ok: Monaco takes a pixel number, not CSS; matches --fs-body
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            automaticLayout: true,
            wordWrap: "on",
            tabSize: 2,
            padding: { top: 12, bottom: 12 },
          }}
          onChange={(next) => onChange(next ?? "")}
          onMount={handleMount}
        />
      </div>
    </div>
  );
}
