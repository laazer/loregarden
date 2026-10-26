/**
 * The topbar's one search box: ⌘K from any page, then type to jump to a page
 * or open a ticket.
 *
 * It answers "where is the thing I'm thinking of?" without knowing which page
 * holds it. Pages match by name from the rail's own catalog, so they appear on
 * the first keystroke; tickets match by id or title through the server's
 * search, across every workspace, once there are two characters to go on.
 * Initiatives, chats, branches and tabs join in the next change.
 *
 * It is an ARIA combobox: the field owns focus, ↑/↓ move the active option
 * (announced through `aria-activedescendant`), Enter opens it, and Escape
 * clears the text, then leaves the field.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { TicketSummary } from "../api/types";
import { ticketPath } from "../lib/appNavigation";
import { ticketStateChip } from "../lib/ticketStates";
import { describeError } from "../state/toastStore";
import { matchPages } from "../lib/topbarSearch";
import type { SidebarPageDef } from "./appSidebarPages";
import { Input } from "./ui/Input";
import { StatusChip } from "./ui/StatusChip";

/** Below this, a ticket search would match half the board. */
const TICKET_SEARCH_MIN_CHARS = 2;
const TICKET_RESULT_LIMIT = 8;
/** Long enough to skip the keystrokes of a word being typed, short enough to feel live. */
const TICKET_SEARCH_DEBOUNCE_MS = 200;

type Option =
  | { kind: "page"; key: string; page: SidebarPageDef }
  | { kind: "ticket"; key: string; ticket: TicketSummary };

/**
 * ⌘K belongs to the editor and the terminal while they have focus: Monaco binds
 * it as a chord prefix (⌘K ⌘C), and a shell reads Ctrl+K as kill-line.
 */
function keyIsForAnEditor(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(".monaco-editor, .xterm") !== null;
}

function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return settled;
}

export function TopbarSearch() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const [active, setActive] = useState(0);

  const ticketQuery = useDebounced(query.trim(), TICKET_SEARCH_DEBOUNCE_MS);
  const searchTickets = ticketQuery.length >= TICKET_SEARCH_MIN_CHARS;
  const tickets = useQuery({
    queryKey: ["topbar-search", "tickets", ticketQuery],
    queryFn: () => api.tickets({ search: ticketQuery, limit: TICKET_RESULT_LIMIT }),
    enabled: searchTickets,
    staleTime: 15_000,
    // The failure is drawn in the results, where the operator is looking.
    meta: { suppressErrorToast: true },
  });

  const pages = useMemo(() => matchPages(query), [query]);
  const ticketRows = useMemo(
    () => (searchTickets ? (tickets.data ?? []) : []),
    [searchTickets, tickets.data],
  );
  const options: Option[] = useMemo(
    () => [
      ...pages.map((page): Option => ({ kind: "page", key: `page-${page.key}`, page })),
      ...ticketRows.map((ticket): Option => ({ kind: "ticket", key: `ticket-${ticket.id}`, ticket })),
    ],
    [pages, ticketRows],
  );

  // A new result set starts at its first option rather than an index that may
  // no longer exist.
  useEffect(() => setActive(0), [query, tickets.data]);

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey)) return;
      if (event.altKey || event.shiftKey || keyIsForAnEditor(event.target)) return;
      event.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const open = focused && query.trim() !== "";
  const typing = query.trim() !== ticketQuery;
  const ticketsPending = (searchTickets && tickets.isFetching) || (typing && query.trim().length >= TICKET_SEARCH_MIN_CHARS);
  const activeOption = options[active];

  const choose = (option: Option) => {
    navigate(option.kind === "page" ? option.page.path : ticketPath(option.ticket.id));
    setQuery("");
    inputRef.current?.blur();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!options.length) return;
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => (current + step + options.length) % options.length);
    } else if (event.key === "Enter") {
      if (!activeOption) return;
      event.preventDefault();
      choose(activeOption);
    } else if (event.key === "Escape") {
      event.preventDefault();
      if (query) setQuery("");
      else event.currentTarget.blur();
    }
  };

  const optionId = (option: Option) => `${listId}-${option.key}`;
  const renderOption = (option: Option, index: number) => (
    <li
      key={option.key}
      id={optionId(option)}
      role="option"
      aria-selected={index === active}
      className={`topbar-search-option${index === active ? " is-active" : ""}`}
      // ux-ok: a listbox option under an aria-activedescendant combobox: the
      // input's onKeyDown moves (↑/↓) and chooses (Enter) it, and an option must
      // not be a tab stop of its own, so the click is the pointer's path only.
      // Keep focus in the field so the list stays open through the click.
      onMouseDown={(event) => event.preventDefault()}
      onMouseEnter={() => setActive(index)}
      onFocus={() => setActive(index)}
      onClick={() => choose(option)}
    >
      {option.kind === "page" ? (
        <>
          <span className="topbar-search-option-icon">{option.page.icon}</span>
          <span className="topbar-search-option-title">{option.page.label}</span>
          <span className="topbar-search-option-meta">Page</span>
        </>
      ) : (
        <>
          <span className="topbar-search-option-id">{option.ticket.external_id}</span>
          <span className="topbar-search-option-title">{option.ticket.title}</span>
          <StatusChip {...ticketStateChip(option.ticket.state)} />
        </>
      )}
    </li>
  );

  let ticketNote: string | null = null;
  if (query.trim().length < TICKET_SEARCH_MIN_CHARS) ticketNote = "Type another letter to search tickets";
  else if (ticketsPending) ticketNote = "Searching tickets…";
  else if (tickets.isError) ticketNote = `Couldn't search tickets: ${describeError(tickets.error, "request failed")}`;
  else if (!ticketRows.length) ticketNote = pages.length ? "No tickets match" : null;

  return (
    <div className="topbar-search">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
      <Input
        ref={inputRef}
        role="combobox"
        aria-label="Search pages and tickets"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && activeOption ? optionId(activeOption) : undefined}
        placeholder="Search pages and tickets"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onKeyDown={onKeyDown}
      />
      <kbd aria-hidden="true">⌘K</kbd>
      {open ? (
        <div className="topbar-search-popover">
          <ul id={listId} role="listbox" aria-label="Search results" className="topbar-search-list">
            {options.map(renderOption)}
          </ul>
          {!options.length && !ticketsPending && !tickets.isError && query.trim().length >= TICKET_SEARCH_MIN_CHARS ? (
            <p className="topbar-search-note" role="status">
              Nothing matches “{query.trim()}”. Search looks at page names and ticket ids and titles.
            </p>
          ) : ticketNote ? (
            <p
              className={`topbar-search-note${tickets.isError && !ticketsPending ? " is-error" : ""}`}
              role="status"
            >
              {ticketNote}
            </p>
          ) : null}
        </div>
      ) : (
        <ul id={listId} role="listbox" aria-label="Search results" hidden />
      )}
    </div>
  );
}
