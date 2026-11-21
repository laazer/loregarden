import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { localInstancesApi } from "../../api/localInstancesApi";
import type { LocalInstance } from "../../api/localInstancesTypes";
import { reviewApi, type ReviewRequest, type TryResult } from "../../api/reviewApi";
import { INSTANCES_KEY } from "../../hooks/useLocalInstances";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { StatusChip } from "../ui/StatusChip";

/** A server instance a request can go to: ready, and on this machine. */
function serverOptions(instances: LocalInstance[]) {
  return instances.filter((i) => i.state === "ready" && i.kind === "server");
}

/**
 * Endpoint checks an agent left for the reviewer: each request is shown in
 * full (method, path, body) before it is sent, so a click never fires
 * something the reviewer could not read first. The base is a local server
 * the reviewer picks or types; the server refuses anything off this machine.
 */
export function ReviewRequests({
  requests,
  workspaceSlug,
}: {
  requests: ReviewRequest[];
  workspaceSlug: string;
}) {
  const baseId = useId();
  const instances = useQuery({ queryKey: INSTANCES_KEY, queryFn: localInstancesApi.list });
  const servers = serverOptions(instances.data?.instances ?? []);
  const suggested =
    servers.find((s) => s.project === workspaceSlug)?.url ?? servers[0]?.url ?? "http://127.0.0.1:8000";
  const [base, setBase] = useState<string | null>(null);
  const baseUrl = base ?? suggested;

  if (requests.length === 0) return null;
  return (
    <section className="review-section" aria-labelledby={`${baseId}-title`}>
      <h3 id={`${baseId}-title`} className="review-section-title">
        Endpoint checks ({requests.length})
      </h3>
      <div className="review-base-row">
        <label htmlFor={`${baseId}-base`}>Send to</label>
        <Input
          id={`${baseId}-base`}
          list={`${baseId}-servers`}
          value={baseUrl}
          onChange={(event) => setBase(event.target.value)}
          spellCheck={false}
        />
        <datalist id={`${baseId}-servers`}>
          {servers.map((s) => (
            <option key={s.url} value={s.url}>
              {s.project} · {s.name}
            </option>
          ))}
        </datalist>
      </div>
      <ol className="review-requests">
        {requests.map((req, index) => (
          <RequestRow key={`${req.method}-${req.path}-${index}`} request={req} baseUrl={baseUrl} />
        ))}
      </ol>
    </section>
  );
}

function verdict(request: ReviewRequest, result: TryResult) {
  if (!result.ok) return { tone: "bad" as const, label: "No response" };
  if (request.expect_status === null) return { tone: "idle" as const, label: String(result.status) };
  return result.status === request.expect_status
    ? { tone: "done" as const, label: `${result.status} as expected` }
    : { tone: "bad" as const, label: `${result.status}, expected ${request.expect_status}` };
}

function RequestRow({ request, baseUrl }: { request: ReviewRequest; baseUrl: string }) {
  const send = useMutation({
    meta: { errorTitle: `Send ${request.method} ${request.path}` },
    mutationFn: () =>
      reviewApi.tryRequest({ base_url: baseUrl, method: request.method, path: request.path, body: request.body }),
  });
  const result = send.data;
  const chip = result ? verdict(request, result) : null;
  return (
    <li className="review-request">
      <div className="review-request-head">
        <span className="review-request-name">{request.name}</span>
        <code className="review-mono">
          {request.method} {request.path}
        </code>
        <Button variant="secondary" compact disabled={send.isPending} onClick={() => send.mutate()}>
          {send.isPending ? "Sending…" : "Send"}
        </Button>
        {chip ? <StatusChip tone={chip.tone} label={chip.label} /> : null}
      </div>
      {request.body ? (
        // A long body scrolls; the tab stop lets a keyboard scroll it.
        <pre
          className="review-request-body scroll-focus-ring"
          role="group"
          tabIndex={0}
          aria-label={`Request body for ${request.name}`}
        >
          {request.body}
        </pre>
      ) : null}
      {result ? (
        <div className="review-response">
          <span className="review-muted">
            {result.elapsed_ms} ms{result.content_type ? ` · ${result.content_type}` : ""}
            {result.truncated ? " · truncated" : ""}
          </span>
          <pre>{result.ok ? result.body || "(empty body)" : result.error}</pre>
        </div>
      ) : null}
    </li>
  );
}
