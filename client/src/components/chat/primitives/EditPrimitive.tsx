import { useState } from "react";
import Editor from "@monaco-editor/react";

import type { EditPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import {
  OpenAgentStudioButton,
  OpenGateStudioButton,
  OpenIdeButton,
  OpenWorkflowStudioButton,
} from "./ResourceActionButton";

export function EditPrimitive({
  part,
  onSave,
}: {
  part: EditPart;
  onSave?: (content: string) => void | Promise<void>;
}) {
  const [draft, setDraft] = useState(part.content ?? "");
  const [saving, setSaving] = useState(false);
  const dirty = draft !== (part.content ?? "");
  const resourceAction =
    part.target === "agent" && part.target_id ? (
      <OpenAgentStudioButton slug={part.target_id} />
    ) : part.target === "workflow" && part.target_id ? (
      <OpenWorkflowStudioButton slug={part.target_id} />
    ) : part.target === "gate" ? (
      <OpenGateStudioButton />
    ) : part.target === "terminal" ? (
      <OpenIdeButton />
    ) : null;

  return (
    <PrimitiveCard
      title={part.title ?? `Edit ${part.target ?? "text"}`}
      subtitle={part.target_id ?? part.language ?? undefined}
      actions={
        <>
          {resourceAction}
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
            className="lg-primitive-run-btn lg-primitive-run-btn--play"
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
      }
    >
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
    </PrimitiveCard>
  );
}
