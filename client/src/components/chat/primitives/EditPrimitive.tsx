import { useMemo, useState } from "react";
import Editor, { DiffEditor } from "@monaco-editor/react";

import { InlineCodeDiffReview } from "../../InlineCodeDiffReview";
import {
  buildTextDiffArtifact,
  formatEditCommentsForChat,
} from "../../../lib/textDiff";
import type { EditPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenEditorFileButton, OpenIdeButton } from "./ResourceActionButton";

export function EditPrimitive({
  part,
  onSave,
  onSubmit,
}: {
  part: EditPart;
  onSave?: (content: string) => void | Promise<void>;
  onSubmit?: (content: string) => void;
}) {
  const original = part.original;
  const hasDiff = original !== undefined && original !== null;
  const [draft, setDraft] = useState(part.content ?? "");
  const [mode, setMode] = useState<"review" | "edit">(hasDiff ? "review" : "edit");
  const [saving, setSaving] = useState(false);
  const [sent, setSent] = useState(false);
  const dirty = draft !== (part.content ?? "");

  const filePath = part.path?.trim() || null;
  const path =
    filePath ||
    (part.target && part.target_id ? `${part.target}/${part.target_id}` : "proposed edit");
  const title = part.title ?? `Edit ${part.target ?? "text"}`;

  const diff = useMemo(
    () => (hasDiff ? buildTextDiffArtifact(path, original ?? "", draft) : null),
    [hasDiff, path, original, draft],
  );

  // Edit cards open the file in the Editor page — never Agent/Workflow Studio.
  const resourceAction = filePath ? (
    <OpenEditorFileButton path={filePath} workspaceSlug={part.workspace_slug ?? undefined} />
  ) : (
    <OpenIdeButton workspaceSlug={part.workspace_slug ?? undefined} />
  );

  const reviewActions =
    hasDiff ? (
      <div className="lg-primitive-edit-mode-toggle" role="group" aria-label="Edit view">
        <button
          type="button"
          className={
            mode === "review"
              ? "lg-primitive-run-btn lg-primitive-run-btn--confirm"
              : "lg-primitive-run-btn"
          }
          aria-pressed={mode === "review"}
          onClick={() => setMode("review")}
        >
          Review
        </button>
        <button
          type="button"
          className={
            mode === "edit" ? "lg-primitive-run-btn lg-primitive-run-btn--confirm" : "lg-primitive-run-btn"
          }
          aria-pressed={mode === "edit"}
          onClick={() => setMode("edit")}
        >
          Edit
        </button>
      </div>
    ) : null;

  const saveActions = (
    <>
      <button
        type="button"
        className="lg-primitive-run-btn"
        disabled={!dirty || saving}
        onClick={() => setDraft(part.content ?? "")}
      >
        Discard
      </button>
      <button
        type="button"
        className="lg-primitive-run-btn lg-primitive-run-btn--confirm"
        disabled={!dirty || saving || !onSave}
        onClick={() => {
          if (!onSave) return;
          setSaving(true);
          void Promise.resolve(onSave(draft)).finally(() => setSaving(false));
        }}
      >
        {saving ? "Saving…" : "Save"}
      </button>
    </>
  );

  return (
    <PrimitiveCard
      title={title}
      subtitle={part.path ?? part.target_id ?? part.language ?? undefined}
      resourceAction={resourceAction}
      tone={sent ? "ok" : hasDiff ? "accent" : "default"}
      meta={
        hasDiff ? (
          <span>{sent ? "Comments sent" : "Proposed edit — comment a line to chat"}</span>
        ) : null
      }
      actions={
        <>
          {reviewActions}
          {mode === "edit" ? saveActions : null}
        </>
      }
    >
      {hasDiff && mode === "review" && diff ? (
        <div className="lg-primitive-edit-review">
          <InlineCodeDiffReview
            localMode
            diff={diff}
            diffSummary={{
              files: diff.files,
              add: diff.add,
              del: diff.del,
            }}
            submitActionLabel="Send comments to chat"
            onSubmitLocal={async ({ comments, instructions }) => {
              if (!onSubmit) {
                throw new Error("Chat is not available to receive these comments.");
              }
              onSubmit(
                formatEditCommentsForChat({
                  title,
                  path,
                  comments,
                  instructions,
                }),
              );
              setSent(true);
            }}
          />
        </div>
      ) : hasDiff ? (
        <div className="lg-primitive-editor lg-primitive-editor--diff">
          <DiffEditor
            height="100%"
            language={part.language ?? "markdown"}
            theme="vs-dark"
            original={original ?? ""}
            modified={draft}
            onMount={(editor) => {
              const modified = editor.getModifiedEditor();
              modified.onDidChangeModelContent(() => {
                setDraft(modified.getValue());
              });
            }}
            options={{
              renderSideBySide: true,
              readOnly: false,
              originalEditable: false,
              minimap: { enabled: false },
              fontSize: 12,
              wordWrap: "on",
              scrollBeyondLastLine: false,
              automaticLayout: true,
            }}
          />
        </div>
      ) : (
        <div className="lg-primitive-editor">
          <Editor
            height="100%"
            language={part.language ?? "markdown"}
            theme="vs-dark"
            value={draft}
            onChange={(value) => setDraft(value ?? "")}
            options={{
              minimap: { enabled: false },
              fontSize: 12,
              wordWrap: "on",
              scrollBeyondLastLine: false,
            }}
          />
        </div>
      )}
    </PrimitiveCard>
  );
}
