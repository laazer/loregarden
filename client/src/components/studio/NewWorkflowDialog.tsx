/**
 * Workflow Studio → New workflow…: the shared create dialog, with a workflow's
 * words and one more field, what to start from.
 *
 * A workflow is mostly its stages, so a new one starts either blank (stages are
 * added in the editor) or as a copy of an existing workflow's stages and
 * routes. A draft from "Describe it" brings its own stages and takes precedence.
 * It is created unpublished either way: no ticket runs on it until it is
 * published from the editor.
 */

import { useId, useState } from "react";

import { api } from "../../api/client";
import type { StudioGeneratedWorkflow, StudioWorkflow } from "../../api/types";
import { Select } from "../ui/Select";
import { StudioCreateDialog, type StudioCreateSpec } from "./StudioCreateDialog";

const BLANK = "";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (slug: string) => void;
  workflows: readonly StudioWorkflow[];
}

export function NewWorkflowDialog({ open, onClose, onCreated, workflows }: Props) {
  const id = useId();
  const [startFrom, setStartFrom] = useState(BLANK);
  const source = workflows.find((workflow) => workflow.slug === startFrom) ?? null;
  const reset = () => setStartFrom(BLANK);

  const spec: StudioCreateSpec<StudioGeneratedWorkflow> = {
    noun: "workflow",
    effect: "Creates the workflow unpublished, then opens its editor for its stages.",
    describePlaceholder: "What it is for, and the stages a ticket goes through",
    draftNote: "Drafted. Its stages go in with it, in place of the start below.",
    slugHint: "How tickets and the API name it. It cannot be changed later.",
    listKey: ["studio-workflows"],
    generate: (brief) => api.generateStudioWorkflow(brief),
    create: (fields, draft) => {
      const stages = draft?.stages.length ? draft.stages : (source?.stages ?? []);
      const transitions = draft?.stages.length ? [] : (source?.transitions ?? []);
      return api.createStudioWorkflow({
        ...fields,
        stages: stages.map((stage, index) => ({ ...stage, order: index + 1 })),
        ...(transitions.length ? { transitions } : {}),
      });
    },
    extra: (disabled) => (
      <>
        <label className="studio-create-field" htmlFor={`${id}-start`}>
          Start from
        </label>
        <Select
          id={`${id}-start`}
          value={startFrom}
          onChange={(event) => setStartFrom(event.target.value)}
          disabled={disabled}
        >
          <option value={BLANK}>Blank: no stages yet</option>
          {workflows.map((workflow) => (
            <option key={workflow.slug} value={workflow.slug}>
              A copy of {workflow.name} ({workflow.stages.length} stages)
            </option>
          ))}
        </Select>
      </>
    ),
  };

  return (
    <StudioCreateDialog
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      onCreated={(slug) => {
        reset();
        onCreated(slug);
      }}
      spec={spec}
    />
  );
}
