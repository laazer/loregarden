/**
 * The topbar's one search box: ⌘K from any page, then type to jump to a page,
 * a tab, a ticket, an initiative, a chat thread or a branch.
 *
 * It answers "where is the thing I'm thinking of?" without knowing which page
 * holds it. What each source searches, and what it costs, is
 * `useTopbarSearchResults`; this component draws the results, grouped, and
 * owns the keyboard.
 *
 * It is an ARIA combobox: the field owns focus, ↑/↓ move the active option
 * across every group (announced through `aria-activedescendant`), Enter opens
 * it, and Escape clears the text, then leaves the field.
 */

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { useTopbarSearchResults, type SearchResult } from "../hooks/useTopbarSearchResults";
import type { SearchGroup } from "../lib/topbarSearch";
import { Input } from "./ui/Input";
import { StatusChip } from "./ui/StatusChip";

/**
 * ⌘K belongs to the editor and the terminal while they have focus: Monaco binds
 * it as a chord prefix (⌘K ⌘C), and a shell reads Ctrl+K as kill-line.
 */
function keyIsForAnEditor(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(".monaco-editor, .xterm") !== null;
}

export function TopbarSearch() {
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const [active, setActive] = useState(0);

  const open = focused && query.trim() !== "";
  const { results, pending, errors, complete } = useTopbarSearchResults(query, open);

  // Groups in the order results arrive, which is the order they are drawn;
  // `active` indexes the flat list, so ↑/↓ cross from one group to the next.
  const groups = useMemo(() => {
    const byGroup = new Map<SearchGroup, { result: SearchResult; index: number }[]>();
    results.forEach((result, index) => {
      const rows = byGroup.get(result.group) ?? [];
      rows.push({ result, index });
      byGroup.set(result.group, rows);
    });
    return [...byGroup.entries()];
  }, [results]);

  // A new result set starts at its first option rather than an index that may
  // no longer exist.
  useEffect(() => setActive(0), [query, results.length]);

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

  const activeResult = results[active];

  const choose = (result: SearchResult) => {
    result.go();
    setQuery("");
    inputRef.current?.blur();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!results.length) return;
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => (current + step + results.length) % results.length);
    } else if (event.key === "Enter") {
      if (!activeResult) return;
      event.preventDefault();
      choose(activeResult);
    } else if (event.key === "Escape") {
      event.preventDefault();
      if (query) setQuery("");
      else event.currentTarget.blur();
    }
  };

  const optionId = (result: SearchResult) => `${listId}-${result.key}`;
  const renderOption = ({ result, index }: { result: SearchResult; index: number }) => (
    <div
      key={result.key}
      id={optionId(result)}
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
      onClick={() => choose(result)}
    >
      {result.icon ? <span className="topbar-search-option-icon">{result.icon}</span> : null}
      {result.code ? <span className="topbar-search-option-id">{result.code}</span> : null}
      <span className="topbar-search-option-title">{result.title}</span>
      {result.chip ? <StatusChip {...result.chip} /> : null}
      {result.meta ? <span className="topbar-search-option-meta">{result.meta}</span> : null}
    </div>
  );

  const nothing = complete && !pending && !results.length && !errors.length;

  return (
    <div className="topbar-search">
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        aria-hidden
      >
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
      <Input
        ref={inputRef}
        role="combobox"
        aria-label="Search pages, tickets and more"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && activeResult ? optionId(activeResult) : undefined}
        placeholder="Search pages, tickets, initiatives, chats…"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onKeyDown={onKeyDown}
      />
      <kbd aria-hidden="true">⌘K</kbd>
      {open ? (
        <div className="topbar-search-popover">
          <div
            id={listId}
            role="listbox"
            aria-label="Search results"
            className="topbar-search-list"
          >
            {groups.map(([group, rows]) => (
              <div key={group} role="group" aria-label={group}>
                <div className="topbar-search-group-label" aria-hidden="true">
                  {group}
                </div>
                {rows.map(renderOption)}
              </div>
            ))}
          </div>
          {nothing ? (
            <p className="topbar-search-note" role="status">
              Nothing matches “{query.trim()}”. Search looks at page and tab names, ticket and
              initiative ids and titles, chat threads in this chat scope, and this workspace's
              branches.
            </p>
          ) : null}
          {!complete && query.trim().length < 2 ? (
            <p className="topbar-search-note" role="status">
              Type another letter to search tickets and branches
            </p>
          ) : null}
          {pending ? (
            <p className="topbar-search-note" role="status">
              {pending}
            </p>
          ) : null}
          {errors.map((error) => (
            <p key={error} className="topbar-search-note is-error" role="status">
              {error}
            </p>
          ))}
        </div>
      ) : (
        <div id={listId} role="listbox" aria-label="Search results" hidden />
      )}
    </div>
  );
}
