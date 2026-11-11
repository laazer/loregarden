import { useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import type { ResearchQuestionSummary } from "../../api/referenceResearchApi";
import { shortDate } from "../../lib/referenceLibrary";
import { researchQuestionsKey } from "../../lib/researchCandidates";
import { describeError } from "../../state/toastStore";
import { useUiStore } from "../../state/uiStore";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import "../research/Research.css";

/** Open a thread full-page: bind it under its workspace, then go to the chat. */
function useOpenThread(workspace: string) {
  const navigate = useNavigate();
  const setChatScope = useUiStore((state) => state.setChatScope);
  const setSessionId = useUiStore((state) => state.setBaxterChatSessionId);
  return (threadId: string) => {
    // Scope first: switching scope clears the bound thread.
    setChatScope(workspace);
    setSessionId(threadId);
    navigate("/chat");
  };
}

function QuestionRow({ question, onOpen }: { question: ResearchQuestionSummary; onOpen: () => void }) {
  const tags = [
    `${question.candidates} candidate${question.candidates === 1 ? "" : "s"}`,
    question.run_count ? `run ${question.run_count}` : "not run yet",
    question.last_run_at ? shortDate(question.last_run_at) : "",
  ].filter(Boolean);
  return (
    <li className="rs-question">
      <Button variant="plain" className="rs-question-open" onClick={onOpen}>
        {question.question}
      </Button>
      <span className="rs-summary">{tags.join(" · ")}</span>
    </li>
  );
}

/**
 * The library's research: the capability questions this workspace has asked,
 * each opening its thread, and a way to start a new one. A research thread
 * checks claims in source and puts candidates on a table it can queue from.
 */
export function ReferenceResearchButton({ workspace }: { workspace: string }) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const openThread = useOpenThread(workspace);
  const questions = useQuery({
    queryKey: researchQuestionsKey(workspace),
    queryFn: () => api.researchQuestions(workspace),
    enabled: open,
  });
  const start = useMutation({
    mutationFn: async () => {
      const thread = await api.createBaxterChatSession(workspace, "Research");
      await api.setBaxterChatMode(workspace, thread.id, "research");
      return thread.id;
    },
    onSuccess: (threadId) => {
      setOpen(false);
      openThread(threadId);
    },
  });

  let body;
  if (questions.isPending) {
    body = <PaneSkeleton variant="list" rows={4} label="Loading research questions…" />;
  } else if (questions.isError) {
    body = (
      <p className="rs-error" role="alert">
        Research questions did not load: {describeError(questions.error, "the request failed")}.
      </p>
    );
  } else if (questions.data.length === 0) {
    body = (
      <p className="rs-empty">
        No research questions yet. Start a research thread and ask which projects do something, such as
        “which repos generate a skeleton for an unrigged mesh?”. Baxter checks each claim in the project's
        source, and you can queue the ones worth a survey pass from its table.
      </p>
    );
  } else {
    body = (
      <ul className="rs-question-list" aria-label="Research questions">
        {questions.data.map((question) => (
          <QuestionRow
            key={question.id}
            question={question}
            onOpen={() => {
              setOpen(false);
              openThread(question.thread_id);
            }}
          />
        ))}
      </ul>
    );
  }

  return (
    <>
      <Button
        variant="secondary"
        compact
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        Research
      </Button>
      <ModalShell open={open} onDismiss={start.isPending ? undefined : () => setOpen(false)} labelledBy={titleId}>
        <div className="modal-header">
          <div>
            <div className="state-label">Reference library</div>
            <h2 id={titleId} className="modal-title">
              Research
            </h2>
            <p className="modal-subtitle">Which projects do what you need, checked in their source.</p>
          </div>
        </div>
        <div className="modal-body">
          {body}
          {start.error ? (
            <p className="rs-error" role="alert">
              The research thread was not started: {describeError(start.error, "request failed")}
            </p>
          ) : null}
        </div>
        <div className="modal-footer">
          <Button variant="secondary" disabled={start.isPending} onClick={() => setOpen(false)}>
            Close
          </Button>
          <Button variant="primary" disabled={start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? "Starting…" : "New research thread"}
          </Button>
        </div>
      </ModalShell>
    </>
  );
}
