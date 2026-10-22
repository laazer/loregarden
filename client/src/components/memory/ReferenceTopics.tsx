import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { SurveyTopic, TopicView } from "../../api/referenceLibraryApi";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";

/** What people commonly survey for; the catalog is the server's (`topics.py`). */
export function useTopics() {
  return useQuery({
    queryKey: ["reference-topics"],
    queryFn: () => api.referenceTopics(),
    staleTime: Infinity,
  });
}

/**
 * One pressed chip per topic. Picking one hands the caller the topic (to fill a
 * form, or to search for); picking it again hands back null.
 */
export function TopicChips({
  selected,
  onPick,
  label,
}: {
  selected: SurveyTopic | null;
  onPick: (topic: TopicView | null) => void;
  label: string;
}) {
  const topics = useTopics();
  if (topics.isPending) return <p className="rl-muted">Loading topics…</p>;
  if (topics.isError) {
    return (
      <p className="rl-warn" role="status">
        Topics did not load ({describeError(topics.error, "the request failed")}); type what to look for instead.
      </p>
    );
  }
  if (topics.data.length === 0) {
    return <p className="rl-muted">No topics are set up; type what to look for instead.</p>;
  }
  return (
    <div className="rl-topics" role="group" aria-label={label}>
      {topics.data.map((topic) => {
        const on = selected === topic.id;
        return (
          <Button
            key={topic.id}
            variant="plain"
            compact
            className={`rl-chip${on ? " rl-chip--on" : ""}`}
            aria-pressed={on}
            title={topic.question}
            onClick={() => onPick(on ? null : topic)}
          >
            {topic.label}
          </Button>
        );
      })}
    </div>
  );
}
