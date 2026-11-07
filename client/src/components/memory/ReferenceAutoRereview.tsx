import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { AutoRereviewSettings, AutoRereviewSettingsRequest } from "../../api/referenceLibraryApi";
import { memoryPath } from "../../lib/appNavigation";
import { recordFocusIntent } from "../../lib/focusIntent";
import { AUTO_REREVIEW_QUERY_KEY } from "../../lib/referenceLibrary";
import { describeError, pushToast } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";

/**
 * The toolbar button that says whether auto re-review is on and opens its
 * settings. `open`/`onOpen`/`onClose` are the library's, so the sidebar quick
 * action (`?settings=auto-rereview`) opens the same dialog.
 */
export function ReferenceAutoRereviewButton({
  open,
  onOpen,
  onClose,
  search,
}: {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  search: string;
}) {
  const settings = useQuery({ queryKey: AUTO_REREVIEW_QUERY_KEY, queryFn: api.autoRereviewSettings });
  let state = "…";
  if (settings.isError) state = "unknown";
  else if (settings.data) state = settings.data.enabled ? "on" : "off";
  return (
    <>
      <Button variant="secondary" compact aria-haspopup="dialog" aria-expanded={open} onClick={onOpen}>
        Auto re-review: {state}
      </Button>
      {open ? <ReferenceAutoRereviewDialog onClose={onClose} search={search} /> : null}
    </>
  );
}

function clampInt(raw: string, max: number): number | null {
  const value = Number(raw);
  return Number.isInteger(value) && value >= 1 && value <= max ? value : null;
}

const MIN_NEW_COMMITS_MAX = 100_000;

/**
 * Auto re-review for the whole library: whether a reviewed repository that has
 * moved is queued again on its own, how far it must move first, and how many of
 * its newest commits the re-review reads. Lists the sources a person took out,
 * each with a way back in.
 */
function ReferenceAutoRereviewDialog({ onClose, search }: { onClose: () => void; search: string }) {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: AUTO_REREVIEW_QUERY_KEY, queryFn: api.autoRereviewSettings });
  const ids = { title: useId(), enabled: useId(), threshold: useId(), cap: useId(), hint: useId() };
  const [draft, setDraft] = useState<{ enabled: boolean; threshold: string; cap: string } | null>(null);
  const [failure, setFailure] = useState("");

  // Fill the form once, from the first answer; later refetches leave edits alone.
  useEffect(() => {
    if (settings.data && draft === null) {
      setDraft({
        enabled: settings.data.enabled,
        threshold: String(settings.data.min_new_commits),
        cap: String(settings.data.max_commits_per_review),
      });
    }
  }, [settings.data, draft]);

  const save = useMutation({
    mutationFn: (body: AutoRereviewSettingsRequest) => api.saveAutoRereviewSettings(body),
    onSuccess: (saved) => {
      queryClient.setQueryData(AUTO_REREVIEW_QUERY_KEY, saved);
      pushToast({
        tone: "success",
        title: saved.enabled ? "Auto re-review is on" : "Auto re-review is off",
        message: saved.enabled
          ? `${saved.due} repositor${saved.due === 1 ? "y is" : "ies are"} due; they are reviewed one at a time.`
          : "Nothing is queued again on its own.",
      });
      onClose();
    },
    onError: (error) => setFailure(describeError(error, "the settings were not saved")),
  });

  const putBack = useMutation({
    mutationFn: (sourceId: string) => api.setSourceAutoRereview(sourceId, false),
    onSuccess: (row) => {
      void queryClient.invalidateQueries({ queryKey: AUTO_REREVIEW_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
      void queryClient.invalidateQueries({ queryKey: ["reference-source", row.id] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Could not put the source back", message: describeError(error) }),
  });

  const ceiling = settings.data?.max_commits_ceiling ?? 100;
  const threshold = draft ? clampInt(draft.threshold, MIN_NEW_COMMITS_MAX) : null;
  const cap = draft ? clampInt(draft.cap, ceiling) : null;
  const invalid = draft !== null && (threshold === null || cap === null);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft || threshold === null || cap === null || save.isPending) return;
    setFailure("");
    save.mutate({ enabled: draft.enabled, min_new_commits: threshold, max_commits_per_review: cap });
  }

  let body;
  if (settings.isPending) {
    body = <p className="rl-muted rl-rereview-note">Loading the settings…</p>;
  } else if (settings.isError || !draft) {
    body = (
      <div role="alert">
        <p className="rl-warn">
          The settings did not load: {describeError(settings.error, "the request failed")}.
        </p>
        <Button variant="secondary" compact disabled={settings.isFetching} onClick={() => void settings.refetch()}>
          {settings.isFetching ? "Retrying…" : "Retry"}
        </Button>
      </div>
    );
  } else {
    body = (
      <>
        <label className="rl-rereview-toggle" htmlFor={ids.enabled}>
          <Input
            id={ids.enabled}
            type="checkbox"
            checked={draft.enabled}
            disabled={save.isPending}
            onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
          />
          <span>Review a reference again once its repository has moved</span>
        </label>
        <div className="rl-field">
          <label htmlFor={ids.threshold}>Re-review after this many new commits</label>
          <Input
            id={ids.threshold}
            type="number"
            inputMode="numeric"
            min={1}
            max={MIN_NEW_COMMITS_MAX}
            value={draft.threshold}
            disabled={save.isPending}
            aria-invalid={threshold === null}
            onChange={(event) => setDraft({ ...draft, threshold: event.target.value })}
          />
        </div>
        <div className="rl-field">
          <label htmlFor={ids.cap}>Read at most this many of the newest commits</label>
          <Input
            id={ids.cap}
            type="number"
            inputMode="numeric"
            min={1}
            max={ceiling}
            value={draft.cap}
            disabled={save.isPending}
            aria-invalid={cap === null}
            aria-describedby={ids.hint}
            onChange={(event) => setDraft({ ...draft, cap: event.target.value })}
          />
          <span id={ids.hint} className={invalid ? "rl-warn" : "rl-muted"} aria-live="polite">
            {invalid
              ? `Whole numbers: at least 1 new commit, and 1 to ${ceiling} commits read.`
              : "Older commits beyond this are skipped; the review starts from the newest."}
          </span>
        </div>
        <RereviewStatus settings={settings.data} search={search} />
        <ExcludedList
          settings={settings.data}
          search={search}
          pending={putBack.isPending ? putBack.variables : null}
          onPutBack={(id) => putBack.mutate(id)}
        />
        {failure ? (
          <p className="rl-warn" role="alert">
            Not saved: {failure}
          </p>
        ) : null}
      </>
    );
  }

  return (
    <ModalShell open onDismiss={save.isPending ? undefined : onClose} labelledBy={ids.title}>
      <form aria-label="Auto re-review" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2 id={ids.title} className="modal-title">
              Auto re-review
            </h2>
            <p className="modal-subtitle">
              For the whole library. A reviewed GitHub repository is queued again once the daily check finds
              it far enough ahead; one re-review runs at a time, the most-moved first.
            </p>
          </div>
          <IconCloseButton disabled={save.isPending} onClick={onClose} />
        </div>
        <div className="modal-body rl-modal-body">{body}</div>
        <div className="modal-footer">
          <Button variant="secondary" disabled={save.isPending} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={!draft || invalid || save.isPending}>
            {save.isPending ? "Saving…" : "Save auto re-review"}
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}

function RereviewStatus({ settings, search }: { settings: AutoRereviewSettings; search: string }) {
  const due = `${settings.due} repositor${settings.due === 1 ? "y is" : "ies are"} due at the saved threshold.`;
  if (!settings.in_flight) return <p className="rl-muted rl-rereview-note">{due}</p>;
  const { in_flight: run } = settings;
  return (
    <p className="rl-muted rl-rereview-note">
      {due} Now {run.status}:{" "}
      <Link
        to={{ pathname: memoryPath("library", run.source_id), search }}
        onClick={(event) => recordFocusIntent(run.source_id, event.currentTarget)}
      >
        {run.name} pass {run.ordinal}
      </Link>
      .
    </p>
  );
}

function ExcludedList({
  settings,
  search,
  pending,
  onPutBack,
}: {
  settings: AutoRereviewSettings;
  search: string;
  pending: string | null | undefined;
  onPutBack: (id: string) => void;
}) {
  if (settings.excluded.length === 0) {
    return (
      <p className="rl-muted rl-rereview-note">
        Every source is included. Take one out from its page with <strong>Take out of auto re-review</strong>.
      </p>
    );
  }
  return (
    <section aria-label="Taken out of auto re-review">
      <h3 className="rl-rereview-heading">Taken out ({settings.excluded.length})</h3>
      <ul className="rl-rereview-excluded">
        {settings.excluded.map((source) => (
          <li key={source.id}>
            <Link
              to={{ pathname: memoryPath("library", source.id), search }}
              onClick={(event) => recordFocusIntent(source.id, event.currentTarget)}
            >
              {source.name}
            </Link>
            <Button
              variant="plain"
              compact
              disabled={pending === source.id}
              onClick={() => onPutBack(source.id)}
              aria-label={`Put ${source.name} back in auto re-review`}
            >
              {pending === source.id ? "Putting back…" : "Put back"}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
