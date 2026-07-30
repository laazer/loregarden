import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface PageSlot {
  node: HTMLElement | null;
  setNode: (node: HTMLElement | null) => void;
}

const SlotContext = createContext<PageSlot>({ node: null, setNode: () => {} });

/**
 * A screen still owns its title and its controls; it just renders them in the
 * topbar instead of a header of its own. The landing zone sits above
 * `<Routes>`, so the target node travels down by context rather than by prop.
 */
export function TopbarPageSlotProvider({ children }: { children: ReactNode }) {
  const [node, setNode] = useState<HTMLElement | null>(null);
  const value = useMemo(() => ({ node, setNode }), [node]);
  return <SlotContext.Provider value={value}>{children}</SlotContext.Provider>;
}

/** Where page titles and page controls land. Rendered once, by the topbar. */
export function TopbarPageSlot() {
  const { setNode } = useContext(SlotContext);
  return <div className="topbar-page" ref={setNode} />;
}

/**
 * The screen's title and controls, hoisted into the topbar. Renders nothing
 * until the slot exists — the first paint of a route happens before the
 * topbar's node is attached.
 */
export function PageTopbar({ title, children }: { title: ReactNode; children?: ReactNode }) {
  const { node } = useContext(SlotContext);
  if (!node) return null;
  return createPortal(
    <>
      <h1 className="topbar-page-title">{title}</h1>
      {children ? <div className="topbar-page-actions">{children}</div> : null}
    </>,
    node,
  );
}
