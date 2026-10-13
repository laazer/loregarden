/**
 * The app's <select>, with a themed list in place of the one the OS draws.
 *
 * The <select> itself stays the control: it is what callers style, label,
 * focus, submit and test (`value`, `onChange(event.target.value)`, the
 * combobox role, `fireEvent.change` all behave as before). What changes is the
 * menu it opens. A native <select> opens a list drawn by the operating system
 * (macOS's grey menu, outside every token), so opening is intercepted and a
 * listbox drawn from the theme takes its place. Picking an option there sets
 * the <select>'s value and fires a real `change` event, so React's `onChange`
 * cannot tell the difference.
 *
 * Focus never leaves the <select> while the list is open; the highlighted
 * option is announced through `aria-activedescendant`, the ARIA select-only
 * combobox pattern. That also means a dialog's focus trap is never disturbed.
 *
 * The list goes in the top layer (`popover`), which escapes the overflow and
 * transformed ancestors (modal entrances) that would clip or misplace a
 * `position: fixed` panel, while staying inside the caller's DOM subtree, so a
 * dialog's "click outside" check still counts it as inside.
 *
 * A `multiple` select, or one with `size` > 1, is already an inline list the
 * page draws, so it renders unchanged.
 */

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentPropsWithRef,
  type KeyboardEvent,
  type MouseEvent,
  type Ref,
} from "react";

import { useAnchoredPanelPosition } from "../../hooks/useAnchoredPanelPosition";
import "./primitives.css";

export type SelectProps = ComponentPropsWithRef<"select">;

type ListEntry =
  | { kind: "group"; key: string; label: string }
  | { kind: "option"; key: string; index: number; label: string; disabled: boolean; selected: boolean };

/** Typeahead keeps extending its match while keys arrive within this window. */
const TYPEAHEAD_RESET_MS = 500;

/** The <select>'s options as the list should show them, read from the DOM so any child shape works. */
function readEntries(select: HTMLSelectElement): ListEntry[] {
  const entries: ListEntry[] = [];
  let group: HTMLOptGroupElement | null = null;
  Array.from(select.options).forEach((option, index) => {
    const parent = option.parentElement;
    const optgroup = parent?.tagName === "OPTGROUP" ? (parent as HTMLOptGroupElement) : null;
    if (optgroup && optgroup !== group) {
      entries.push({ kind: "group", key: `group-${index}`, label: optgroup.label });
    }
    group = optgroup;
    entries.push({
      kind: "option",
      key: `option-${index}`,
      index,
      label: option.label || option.text,
      disabled: option.disabled || Boolean(optgroup?.disabled),
      selected: option.selected,
    });
  });
  return entries;
}

function enabledIndexes(entries: ListEntry[]): number[] {
  return entries.flatMap((entry) => (entry.kind === "option" && !entry.disabled ? [entry.index] : []));
}

/** Set the value the way a person would, so React and any form see a real change. */
function commitIndex(select: HTMLSelectElement, index: number): void {
  if (select.selectedIndex === index) return;
  select.selectedIndex = index;
  select.dispatchEvent(new Event("input", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

function assignRef<T>(ref: Ref<T> | undefined, value: T | null): void {
  if (typeof ref === "function") ref(value);
  else if (ref) ref.current = value;
}

/** Keys that open a closed list, the way they open the native one. */
const OPEN_KEYS = new Set(["ArrowDown", "ArrowUp", "Enter", " "]);

export function Select(props: SelectProps) {
  if (props.multiple || (props.size ?? 0) > 1) {
    return <select {...props} />;
  }
  return <ThemedSelect {...props} />;
}

function ThemedSelect({ ref, onMouseDown, onKeyDown, onBlur, onChange, ...rest }: SelectProps) {
  const selectRef = useRef<HTMLSelectElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<ListEntry[]>([]);
  const [active, setActive] = useState(-1);
  const [minWidth, setMinWidth] = useState<number>();
  const typeahead = useRef({ text: "", at: 0 });
  const panelStyle = useAnchoredPanelPosition(open, selectRef, listRef, { align: "left" });

  const setSelectRef = useCallback(
    (node: HTMLSelectElement | null) => {
      selectRef.current = node;
      assignRef(ref, node);
    },
    [ref],
  );

  const close = useCallback(() => setOpen(false), []);

  const openList = useCallback(() => {
    const select = selectRef.current;
    if (!select || select.disabled) return;
    const next = readEntries(select);
    const enabled = enabledIndexes(next);
    setEntries(next);
    setMinWidth(select.offsetWidth);
    setActive(enabled.includes(select.selectedIndex) ? select.selectedIndex : (enabled[0] ?? -1));
    setOpen(true);
  }, []);

  const choose = useCallback((index: number) => {
    const select = selectRef.current;
    setOpen(false);
    if (select) commitIndex(select, index);
  }, []);

  // Options can change while the list is open (a list that finishes loading).
  useEffect(() => {
    const select = selectRef.current;
    if (!open || !select) return;
    const observer = new MutationObserver(() => setEntries(readEntries(select)));
    observer.observe(select, { childList: true, subtree: true, attributes: true, characterData: true });
    return () => observer.disconnect();
  }, [open]);

  // A press anywhere but the control or its list closes it, as the native one does.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: globalThis.MouseEvent) => {
      const target = event.target as Node;
      if (selectRef.current?.contains(target) || listRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  useLayoutEffect(() => {
    if (!open || active < 0) return;
    const option = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    // jsdom has no layout, so no scrollIntoView.
    if (typeof option?.scrollIntoView === "function") option.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const setListRef = useCallback((node: HTMLDivElement | null) => {
    listRef.current = node;
    // Into the top layer before the anchoring hook measures it. Browsers without
    // the popover API (jsdom, older WebKit) keep the fixed-position fallback.
    if (node && typeof node.showPopover === "function") node.showPopover();
  }, []);

  const move = (from: number, step: 1 | -1) => {
    const enabled = enabledIndexes(entries);
    if (!enabled.length) return;
    const at = enabled.indexOf(from);
    const next = at === -1 ? (step === 1 ? 0 : enabled.length - 1) : at + step;
    setActive(enabled[Math.min(Math.max(next, 0), enabled.length - 1)]);
  };

  const typeTo = (key: string) => {
    const now = Date.now();
    const state = typeahead.current;
    state.text = now - state.at > TYPEAHEAD_RESET_MS ? key : state.text + key;
    state.at = now;
    const query = state.text.toLowerCase();
    const options = entries.filter(
      (entry): entry is Extract<ListEntry, { kind: "option" }> => entry.kind === "option" && !entry.disabled,
    );
    const start = Math.max(options.findIndex((entry) => entry.index === active), 0);
    // A fresh single letter moves on from the current option; a longer query stays on it while it still matches.
    const offset = state.text.length === 1 ? 1 : 0;
    for (let step = 0; step < options.length; step += 1) {
      const entry = options[(start + offset + step) % options.length];
      if (entry.label.toLowerCase().startsWith(query)) {
        setActive(entry.index);
        return;
      }
    }
  };

  const handleMouseDown = (event: MouseEvent<HTMLSelectElement>) => {
    onMouseDown?.(event);
    if (event.defaultPrevented || event.button !== 0) return;
    // Stops the OS menu; focus is given back by hand because preventing the default withholds it.
    event.preventDefault();
    event.currentTarget.focus();
    if (open) close();
    else openList();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLSelectElement>) => {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;
    if (!open) {
      if (OPEN_KEYS.has(event.key)) {
        event.preventDefault();
        openList();
      }
      return;
    }
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        move(active, 1);
        return;
      case "ArrowUp":
        event.preventDefault();
        move(active, -1);
        return;
      case "Home":
      case "PageUp":
        event.preventDefault();
        setActive(enabledIndexes(entries)[0] ?? -1);
        return;
      case "End":
      case "PageDown":
        event.preventDefault();
        setActive(enabledIndexes(entries).at(-1) ?? -1);
        return;
      case "Enter":
      case " ":
        event.preventDefault();
        if (active >= 0) choose(active);
        else close();
        return;
      case "Escape":
        // The list's press, not the dialog's: a dialog listening on document never sees it.
        event.preventDefault();
        event.stopPropagation();
        close();
        return;
      case "Tab":
        close();
        return;
      default:
        if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
          event.preventDefault();
          typeTo(event.key);
        }
    }
  };

  const activeId = open && active >= 0 ? `${listId}-${active}` : undefined;
  const listLabel = rest["aria-label"];
  const listLabelledBy = listLabel ? undefined : (rest["aria-labelledby"] ?? rest.id);

  return (
    <>
      <select
        {...rest}
        ref={setSelectRef}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? listId : undefined}
        aria-activedescendant={activeId}
        onMouseDown={handleMouseDown}
        onKeyDown={handleKeyDown}
        onBlur={(event) => {
          onBlur?.(event);
          close();
        }}
        // A value committed by any route (typeahead on the closed control, a
        // script, a test driving the <select>) means the list's job is done.
        onChange={(event) => {
          onChange?.(event);
          close();
        }}
      />
      {open ? (
        <div
          ref={setListRef}
          id={listId}
          popover="manual"
          role="listbox"
          aria-label={listLabel}
          aria-labelledby={listLabelledBy}
          className="ui-select-list"
          style={panelStyle ? { ...panelStyle, minWidth } : { visibility: "hidden" }}
        >
          {entries.map((entry) =>
            entry.kind === "group" ? (
              <div key={entry.key} className="ui-select-group" role="presentation">
                {entry.label}
              </div>
            ) : (
              <div
                key={entry.key}
                id={`${listId}-${entry.index}`}
                data-index={entry.index}
                role="option"
                aria-selected={entry.selected}
                aria-disabled={entry.disabled || undefined}
                className="ui-select-option"
                data-active={entry.index === active || undefined}
                // Keeps focus on the <select>, which owns the keyboard while the list is open.
                onMouseDown={(event) => event.preventDefault()}
                // ux-ok: options never take focus; arrow keys on the <select> move this same highlight.
                onMouseEnter={() => !entry.disabled && setActive(entry.index)}
                onClick={() => !entry.disabled && choose(entry.index)}
              >
                {entry.label}
              </div>
            ),
          )}
        </div>
      ) : null}
    </>
  );
}
