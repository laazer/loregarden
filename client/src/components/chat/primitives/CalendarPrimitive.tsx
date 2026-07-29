import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { useUiStore } from "../../../state/uiStore";
import type { CalendarEventItem, CalendarEventPart, CalendarPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenTicketButton } from "./ResourceActionButton";

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  // `new Date("2026-07-29")` is UTC midnight, which is the previous day west of
  // Greenwich — a bare date means that calendar day in the operator's zone.
  const dateOnly = DATE_ONLY.exec(value);
  if (dateOnly) {
    return new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]));
  }
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatTime(value: Date): string {
  return value.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatDayHeading(value: Date): { weekday: string; date: string } {
  return {
    weekday: value.toLocaleDateString(undefined, { weekday: "long" }),
    date: value.toLocaleDateString(undefined, {
      month: "long",
      day: "numeric",
      year: "numeric",
    }),
  };
}

function eventDurationLabel(event: CalendarEventItem): string | null {
  const start = parseDate(event.starts_at);
  const end = parseDate(event.ends_at);
  if (!start || !end) return null;
  const mins = Math.max(0, Math.round((end.getTime() - start.getTime()) / 60_000));
  if (mins < 1) return null;
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `${hours}h ${rem}m` : `${hours}h`;
}

function sortEvents(events: CalendarEventItem[]): CalendarEventItem[] {
  return [...events].sort((a, b) => {
    const aTime = parseDate(a.starts_at)?.getTime() ?? 0;
    const bTime = parseDate(b.starts_at)?.getTime() ?? 0;
    return aTime - bTime;
  });
}

export function CalendarEventPrimitive({ part }: { part: CalendarEventPart }) {
  const event = part.event;
  const starts = parseDate(event.starts_at);
  return (
    <PrimitiveCard
      title={event.title}
      subtitle={[
        event.kind ?? "one_time",
        starts ? formatTime(starts) : event.starts_at,
      ].join(" · ")}
      meta={event.ticket_id ? <span>{event.ticket_id.slice(0, 8)}…</span> : null}
      actions={
        event.ticket_id ? <OpenTicketButton ticketId={event.ticket_id} /> : null
      }
    >
      {event.description ? <p className="lg-primitive-card-sub">{event.description}</p> : null}
    </PrimitiveCard>
  );
}

function DayAgenda({ day, events }: { day: Date; events: CalendarEventItem[] }) {
  const heading = formatDayHeading(day);
  const ordered = sortEvents(events);

  return (
    <div className="lg-primitive-calendar-day">
      <header className="lg-primitive-calendar-day-head">
        <div>
          <p className="lg-primitive-calendar-day-weekday">{heading.weekday}</p>
          <h4 className="lg-primitive-calendar-day-date">{heading.date}</h4>
        </div>
        <p className="lg-primitive-calendar-day-count">
          {ordered.length === 0
            ? "No events"
            : `${ordered.length} event${ordered.length === 1 ? "" : "s"}`}
        </p>
      </header>

      {ordered.length === 0 ? (
        <p className="lg-primitive-calendar-day-empty">
          Nothing scheduled for this day. Runs, queued work, and ticket activity will appear here
          when the workspace calendar has data.
        </p>
      ) : (
        <ol className="lg-primitive-calendar-agenda">
          {ordered.map((event, i) => {
            const starts = parseDate(event.starts_at);
            const ends = parseDate(event.ends_at);
            const duration = eventDurationLabel(event);
            const kind = event.kind ?? "one_time";
            return (
              <li
                key={event.id ?? `${event.title}-${i}`}
                className={`lg-primitive-calendar-agenda-item lg-primitive-calendar-agenda-item--${kind}`}
              >
                <div className="lg-primitive-calendar-agenda-time" aria-hidden={!starts}>
                  <span className="lg-primitive-calendar-agenda-start">
                    {starts ? formatTime(starts) : "—"}
                  </span>
                  {ends ? (
                    <span className="lg-primitive-calendar-agenda-end">{formatTime(ends)}</span>
                  ) : null}
                  {duration ? (
                    <span className="lg-primitive-calendar-agenda-duration">{duration}</span>
                  ) : null}
                </div>
                <div className="lg-primitive-calendar-agenda-body">
                  <div className="lg-primitive-calendar-agenda-title-row">
                    <span className="lg-primitive-calendar-agenda-kind">{kind}</span>
                    <span className="lg-primitive-calendar-agenda-title">{event.title}</span>
                    {event.ticket_id ? (
                      <OpenTicketButton
                        ticketId={event.ticket_id}
                        compact
                        label={`Open ${event.title}`}
                      />
                    ) : null}
                  </div>
                  {event.description ? (
                    <p className="lg-primitive-calendar-agenda-desc">{event.description}</p>
                  ) : null}
                  {event.ticket_id ? (
                    <p className="lg-primitive-calendar-agenda-meta">
                      ticket {event.ticket_id.slice(0, 8)}…
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

function WeekAgenda({ days, events }: { days: Date[]; events: CalendarEventItem[] }) {
  return (
    <div className="lg-primitive-calendar-week" aria-label="Week schedule">
      {days.map((day) => {
        const dayEvents = sortEvents(
          events.filter((event) => {
            const starts = parseDate(event.starts_at);
            return starts ? sameDay(starts, day) : false;
          }),
        );
        const isToday = sameDay(day, new Date());

        return (
          <section
            key={day.toISOString()}
            className={[
              "lg-primitive-calendar-week-day",
              isToday ? "lg-primitive-calendar-week-day--today" : null,
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <header className="lg-primitive-calendar-week-head">
              <div>
                <p className="lg-primitive-calendar-week-weekday">
                  {day.toLocaleDateString(undefined, { weekday: "short" })}
                </p>
                <p className="lg-primitive-calendar-week-date">
                  {day.toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                </p>
              </div>
              <span className="lg-primitive-calendar-week-count">
                {dayEvents.length || "—"}
              </span>
            </header>

            {dayEvents.length ? (
              <ol className="lg-primitive-calendar-week-events">
                {dayEvents.map((event, index) => {
                  const starts = parseDate(event.starts_at);
                  const kind = event.kind ?? "one_time";
                  return (
                    <li
                      key={event.id ?? `${event.title}-${index}`}
                      className={`lg-primitive-calendar-week-event lg-primitive-calendar-week-event--${kind}`}
                    >
                      <div className="lg-primitive-calendar-week-event-meta">
                        <time dateTime={event.starts_at}>
                          {starts ? formatTime(starts) : "—"}
                        </time>
                        <span>{kind.replace("_", " ")}</span>
                      </div>
                      <div className="lg-primitive-calendar-week-event-title-row">
                        <p className="lg-primitive-calendar-week-event-title">{event.title}</p>
                        {event.ticket_id ? (
                          <OpenTicketButton
                            ticketId={event.ticket_id}
                            compact
                            label={`Open ${event.title}`}
                          />
                        ) : null}
                      </div>
                      {event.description ? (
                        <p className="lg-primitive-calendar-week-event-desc">
                          {event.description}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            ) : (
              <p className="lg-primitive-calendar-week-empty">Open</p>
            )}
          </section>
        );
      })}
    </div>
  );
}

function MonthGrid({
  days,
  cursor,
  events,
}: {
  days: Date[];
  cursor: Date;
  events: CalendarEventItem[];
}) {
  return (
    <div className="lg-primitive-calendar-grid">
      {days.map((day) => {
        const dayEvents = events.filter((e) => {
          const starts = parseDate(e.starts_at);
          return starts ? sameDay(starts, day) : false;
        });
        const muted = day.getMonth() !== cursor.getMonth();
        const isToday = sameDay(day, new Date());
        return (
          <div
            key={day.toISOString()}
            className={[
              "lg-primitive-calendar-cell",
              muted ? "lg-primitive-calendar-cell--muted" : "",
              isToday ? "lg-primitive-calendar-cell--today" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <div className="lg-primitive-calendar-daynum">{day.getDate()}</div>
            {dayEvents.slice(0, 3).map((event, i) => (
              <div
                key={event.id ?? `${event.title}-${i}`}
                className={`lg-primitive-calendar-event lg-primitive-calendar-event--${event.kind ?? "one_time"}`}
                title={event.title}
              >
                <span>{event.title}</span>
                {event.ticket_id ? (
                  <OpenTicketButton
                    ticketId={event.ticket_id}
                    compact
                    label={`Open ${event.title}`}
                  />
                ) : null}
              </div>
            ))}
            {dayEvents.length > 3 ? (
              <span className="lg-primitive-calendar-more">
                +{dayEvents.length - 3} more
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function CalendarPrimitive({ part }: { part: CalendarPart }) {
  const workspace = useUiStore((s) => s.workspace);
  const workspaceSlug = workspace && workspace !== "all" ? workspace : undefined;
  const focus = parseDate(part.focus_date) ?? new Date();
  const [view, setView] = useState<"month" | "week" | "day">(part.view ?? "month");
  const [cursor] = useState(focus);

  const range = useMemo(() => {
    if (view === "day") {
      const start = new Date(cursor);
      start.setHours(0, 0, 0, 0);
      const end = addDays(start, 1);
      return { from: start.toISOString(), to: end.toISOString() };
    }
    if (view === "week") {
      const start = addDays(cursor, -cursor.getDay());
      start.setHours(0, 0, 0, 0);
      return { from: start.toISOString(), to: addDays(start, 7).toISOString() };
    }
    const start = startOfMonth(cursor);
    const gridStart = addDays(start, -start.getDay());
    return { from: gridStart.toISOString(), to: addDays(gridStart, 42).toISOString() };
  }, [cursor, view]);

  const remote = useQuery({
    queryKey: ["calendar-events", workspaceSlug, range.from, range.to],
    queryFn: () => api.calendarEvents(workspaceSlug!, range),
    enabled: Boolean(workspaceSlug) && !(part.events && part.events.length > 0),
  });

  const events: CalendarEventItem[] = part.events?.length
    ? part.events
    : (remote.data ?? []).map((e) => ({
        id: e.id,
        title: e.title,
        starts_at: e.starts_at,
        ends_at: e.ends_at,
        kind: e.kind,
        ticket_id: e.ticket_id,
        description: e.description,
      }));

  const days = useMemo(() => {
    if (view === "day") return [new Date(cursor)];
    if (view === "week") {
      const start = addDays(cursor, -cursor.getDay());
      return Array.from({ length: 7 }, (_, i) => addDays(start, i));
    }
    const start = startOfMonth(cursor);
    const gridStart = addDays(start, -start.getDay());
    return Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
  }, [cursor, view]);

  const dayEvents = useMemo(() => {
    if (view !== "day") return [];
    return events.filter((e) => {
      const starts = parseDate(e.starts_at);
      return starts ? sameDay(starts, cursor) : false;
    });
  }, [cursor, events, view]);

  const subtitle =
    view === "day"
      ? cursor.toLocaleDateString(undefined, {
          weekday: "short",
          month: "short",
          day: "numeric",
          year: "numeric",
        })
      : view === "week"
        ? `${days[0].toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
          })} – ${days[6].toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
            year: "numeric",
          })}`
      : cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" });

  return (
    <PrimitiveCard
      title={part.title ?? "Calendar"}
      subtitle={subtitle}
      loading={remote.isLoading && !part.events?.length}
      error={
        remote.error && !part.events?.length
          ? remote.error instanceof Error
            ? remote.error.message
            : "Failed to load events"
          : null
      }
      actions={
        <>
          {(["month", "week", "day"] as const).map((v) => (
            <button
              key={v}
              type="button"
              className="lg-primitive-run-btn"
              aria-pressed={view === v}
              onClick={() => setView(v)}
            >
              {v}
            </button>
          ))}
        </>
      }
    >
      {view === "day" ? (
        <DayAgenda day={cursor} events={dayEvents} />
      ) : view === "week" ? (
        <WeekAgenda days={days} events={events} />
      ) : (
        <MonthGrid days={days} cursor={cursor} events={events} />
      )}
    </PrimitiveCard>
  );
}
