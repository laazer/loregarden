import { useEffect, useRef, type RefObject } from "react";

import { useAnchoredPanelPosition } from "../../hooks/useAnchoredPanelPosition";
import type { ComposerMenuItem } from "../../hooks/useComposerCommands";
import "./ComposerCommands.css";

/**
 * The completion list a `/` or `@` opens next to the composer.
 *
 * Presentational: it renders whatever `useComposerCommands` matched and reports
 * clicks back. Keyboard navigation lives with the input, because the input
 * keeps focus the whole time — this list is never tabbed into.
 *
 * Positioned against the input rather than by CSS: the same composer sits at
 * the bottom of the screen in the action bar and halfway up it in the chat
 * hero, and a menu pinned above would run off the top in the second case.
 */
export function ComposerCommandMenu({
  items,
  activeIndex,
  triggerKind,
  anchorRef,
  onHover,
  onPick,
}: {
  items: ComposerMenuItem[];
  activeIndex: number;
  triggerKind: "slash" | "mention" | null;
  anchorRef: RefObject<HTMLInputElement | HTMLTextAreaElement | null>;
  onHover: (index: number) => void;
  onPick: (item: ComposerMenuItem) => void;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const open = Boolean(items.length && triggerKind);
  const style = useAnchoredPanelPosition(open, anchorRef, panelRef, {
    align: "left",
    matchWidth: true,
  });

  // Arrowing past the fold has to bring the highlight with it, or the list
  // scrolls out from under the selection and Enter picks something unseen.
  useEffect(() => {
    if (!open) return;
    panelRef.current
      ?.querySelectorAll(".lg-composer-menu-item")
      // `scrollIntoView?.` because jsdom has no implementation of it.
      [activeIndex]?.scrollIntoView?.({ block: "nearest" });
  }, [open, activeIndex]);

  if (!open) return null;

  return (
    <div
      ref={panelRef}
      className="lg-composer-menu"
      // Hidden until measured: an unpositioned first paint lands in the corner
      // of the screen and jumps.
      style={style ?? { position: "fixed", visibility: "hidden" }}
      role="listbox"
      aria-label={triggerKind === "slash" ? "Commands and skills" : "Files and folders"}
    >
      <div className="lg-composer-menu-hint">
        {triggerKind === "slash" ? "Commands · skills" : "Files · folders"}
        <span className="lg-composer-menu-keys">↑↓ · ⏎</span>
      </div>
      {items.map((item, index) => (
        <button
          key={item.id}
          type="button"
          role="option"
          aria-selected={index === activeIndex}
          className={`lg-composer-menu-item${index === activeIndex ? " is-active" : ""}`}
          // Mouse down, not click: a click fires after blur, and blurring the
          // composer closes the menu out from under the pointer.
          onMouseDown={(event) => {
            event.preventDefault();
            onPick(item);
          }}
          onMouseEnter={() => onHover(index)}
        >
          {item.kind === "command" ? (
            <>
              <span className="lg-composer-menu-name">/{item.command.name}</span>
              {item.command.aliases.length ? (
                <span className="lg-composer-menu-alias">
                  /{item.command.aliases.join(" · /")}
                </span>
              ) : null}
              <span className="lg-composer-menu-summary">{item.command.summary}</span>
              {item.command.kind === "skill" ? (
                <span className="lg-composer-menu-tag">skill</span>
              ) : null}
            </>
          ) : (
            <>
              <span className="lg-composer-menu-name">
                {item.match.kind === "directory" ? "📁" : "📄"} {item.match.name}
              </span>
              <span className="lg-composer-menu-summary lg-composer-menu-path">
                {item.match.repo_path}
              </span>
            </>
          )}
        </button>
      ))}
    </div>
  );
}
