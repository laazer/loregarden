import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { LibraryDetail } from "../../api/referenceLibraryApi";
import { researchProjectKey } from "../../lib/researchCandidates";
import { describeError } from "../../state/toastStore";
import { ResearchProjectView } from "../research/ResearchProjectView";

/**
 * What research established about this source, capability by capability, with
 * the evidence and a way to correct it. Claims are kept by URL, so ones made
 * before the source was queued show here too.
 *
 * Absent when research never looked at the source: most of the library was
 * added without a research question, and an empty section on each of them
 * would say nothing.
 */
export function ReferenceSourceCapabilities({ detail }: { detail: LibraryDetail }) {
  const url = detail.source.url;
  const project = useQuery({ queryKey: researchProjectKey(url), queryFn: () => api.researchProject(url) });

  if (project.isPending) return null; // one small read; a skeleton for it would only flash
  if (project.isError) {
    return (
      <section className="rl-section" aria-labelledby="rl-capabilities-heading">
        <h3 id="rl-capabilities-heading">Capabilities</h3>
        <p className="rl-warn" role="alert">
          What research found about this source did not load: {describeError(project.error, "the request failed")}.
        </p>
      </section>
    );
  }
  const data = project.data;
  if (data.claims.length === 0 && data.questions.length === 0) return null;
  return (
    <section className="rl-section" aria-labelledby="rl-capabilities-heading">
      <h3 id="rl-capabilities-heading">
        Capabilities
        <span className="count-pill">{data.claims.length}</span>
      </h3>
      {data.questions.length ? (
        <p className="rl-muted">
          From research: {data.questions.map((question) => `“${question.question}”`).join("; ")}
        </p>
      ) : null}
      <ResearchProjectView project={data} onSourcePage />
    </section>
  );
}
