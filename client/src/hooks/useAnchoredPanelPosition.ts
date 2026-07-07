import { useLayoutEffect, useState, type CSSProperties, type RefObject } from "react";

const VIEWPORT_PADDING = 8;
const PANEL_GAP = 6;

type AnchoredPanelOptions = {
  align?: "left" | "right";
  matchWidth?: boolean;
};

export function useAnchoredPanelPosition(
  open: boolean,
  triggerRef: RefObject<HTMLElement | null>,
  panelRef: RefObject<HTMLElement | null>,
  options: AnchoredPanelOptions = {},
): CSSProperties | undefined {
  const { align = "right", matchWidth = false } = options;
  const [style, setStyle] = useState<CSSProperties | undefined>();

  useLayoutEffect(() => {
    if (!open) {
      setStyle(undefined);
      return;
    }

    const update = () => {
      const trigger = triggerRef.current;
      const panel = panelRef.current;
      if (!trigger || !panel) return;

      const triggerRect = trigger.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;

      let top = triggerRect.bottom + PANEL_GAP;
      if (top + panelRect.height > viewportHeight - VIEWPORT_PADDING) {
        const aboveTop = triggerRect.top - PANEL_GAP - panelRect.height;
        if (aboveTop >= VIEWPORT_PADDING) {
          top = aboveTop;
        } else {
          top = Math.max(VIEWPORT_PADDING, viewportHeight - VIEWPORT_PADDING - panelRect.height);
        }
      }

      let left = align === "right" ? triggerRect.right - panelRect.width : triggerRect.left;
      if (left + panelRect.width > viewportWidth - VIEWPORT_PADDING) {
        left = viewportWidth - VIEWPORT_PADDING - panelRect.width;
      }
      if (left < VIEWPORT_PADDING) {
        left = VIEWPORT_PADDING;
      }

      const nextStyle: CSSProperties = {
        position: "fixed",
        top,
        left,
        maxHeight: viewportHeight - VIEWPORT_PADDING * 2,
        overflowY: "auto",
        visibility: "visible",
      };

      if (matchWidth) {
        nextStyle.minWidth = triggerRect.width;
        nextStyle.maxWidth = triggerRect.width;
      }

      setStyle(nextStyle);
    };

    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, align, matchWidth, triggerRef, panelRef]);

  return style;
}
