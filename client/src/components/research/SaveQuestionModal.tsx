import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { ResearchQuestion, SaveQuestionRequest } from "../../api/referenceResearchApi";
import { RESEARCH_QUESTION_ROOT, RESEARCH_QUESTIONS_ROOT } from "../../lib/researchCandidates";
import { describeError, pushToast } from "../../state/toastStore";
import { useAreas } from "../memory/useAreas";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";
import { Select } from "../ui/Select";

/** The re-run choices, in days; "" re-runs only when asked. */
export const INTERVALS: readonly { days: number | null; label: string }[] = [
  { days: null, label: "Only when I ask" },
  { days: 7, label: "Every week" },
  { days: 14, label: "Every 2 weeks" },
  { days: 30, label: "Every 30 days" },
];

export function intervalLabel(days: number | null): string {
  return INTERVALS.find((choice) => choice.days === days)?.label ?? `Every ${days} days`;
}

/**
 * Save a research question: list it in the library's research, pin it to a
 * project area, and choose whether it re-runs on its own. A re-run is a turn in
 * the question's thread, answered with what changed since the last run.
 */
export function SaveQuestionModal({
  open,
  question,
  onClose,
}: {
  open: boolean;
  question: ResearchQuestion;
  onClose: () => void;
}) {
  const titleId = useId();
  const areaId = useId();
  const intervalId = useId();
  const areas = useAreas();
  const [area, setArea] = useState(question.area_id ?? "");
  const [intervalDays, setIntervalDays] = useState(question.interval_days == null ? "" : String(question.interval_days));
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (body: SaveQuestionRequest) => api.saveResearchQuestion(question.id, body),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: [RESEARCH_QUESTION_ROOT] });
      void queryClient.invalidateQueries({ queryKey: [RESEARCH_QUESTIONS_ROOT] });
      pushToast({
        tone: "success",
        title: saved.saved ? "Question saved" : "Question no longer saved",
        message: saved.saved
          ? `${intervalLabel(saved.interval_days)}. It is listed under the library's Research.`
          : "It stays in its thread but leaves the library's list and stops re-running.",
      });
      onClose();
    },
  });
  const submit = (saved: boolean) =>
    save.mutate({ saved, area_id: area || null, interval_days: intervalDays ? Number(intervalDays) : null });

  return (
    <ModalShell open={open} onDismiss={save.isPending ? undefined : onClose} labelledBy={titleId}>
      <div className="modal-header">
        <div>
          <div className="state-label">Research</div>
          <h2 id={titleId} className="modal-title">
            {question.saved ? "Saved question" : "Save this question"}
          </h2>
          <p className="modal-subtitle">“{question.question}”</p>
        </div>
      </div>
      <div className="modal-body">
        <label className="rs-field-label" htmlFor={areaId}>
          Project area (optional)
        </label>
        {areas.isError ? (
          <p className="rs-error" role="alert">
            Areas did not load: {describeError(areas.error, "the request failed")}
          </p>
        ) : (
          <Select
            id={areaId}
            value={area}
            disabled={save.isPending || areas.isPending}
            onChange={(event) => setArea(event.target.value)}
          >
            <option value="">{areas.isPending ? "Loading areas…" : "No area"}</option>
            {(areas.data ?? []).map((choice) => (
              <option key={choice.id} value={choice.id}>
                {choice.workspace_slug} · {choice.name}
              </option>
            ))}
          </Select>
        )}
        <label className="rs-field-label" htmlFor={intervalId}>
          Re-run
        </label>
        <Select
          id={intervalId}
          value={intervalDays}
          disabled={save.isPending}
          onChange={(event) => setIntervalDays(event.target.value)}
        >
          {INTERVALS.map((choice) => (
            <option key={choice.label} value={choice.days == null ? "" : String(choice.days)}>
              {choice.label}
            </option>
          ))}
        </Select>
        <p className="rs-summary">
          A re-run is a research turn in this question's thread. It spends an agent turn each time, and
          answers with what changed since the last run.
        </p>
        {question.schedule_error ? (
          <p className="rs-error" role="alert">
            The last scheduled re-run did not start: {question.schedule_error}
          </p>
        ) : null}
        {save.error ? (
          <p className="rs-error" role="alert">
            Not saved: {describeError(save.error, "request failed")}
          </p>
        ) : null}
      </div>
      <div className="modal-footer">
        <Button variant="secondary" disabled={save.isPending} onClick={onClose}>
          Cancel
        </Button>
        {question.saved ? (
          <Button variant="secondary" disabled={save.isPending} onClick={() => submit(false)}>
            Stop saving
          </Button>
        ) : null}
        <Button variant="primary" disabled={save.isPending} onClick={() => submit(true)}>
          {save.isPending ? "Saving…" : question.saved ? "Update" : "Save question"}
        </Button>
      </div>
    </ModalShell>
  );
}
