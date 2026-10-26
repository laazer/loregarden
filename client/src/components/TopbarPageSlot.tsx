import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Link, useLocation } from "react-router-dom";

import { pageFromPath } from "../lib/appNavigation";
import { SIDEBAR_PAGES } from "./appSidebarPages";

interface PageSlot {
  node: HTMLElement | null;
  setNode: (node: HTMLElement | null) => void;
  crumbNode: HTMLElement | null;
  setCrumbNode: (node: HTMLElement | null) => void;
  /** How many mounted `PageTopbar`s have taken the breadcrumb over. */
  claims: number;
  setClaims: (update: (claims: number) => number) => void;
}

const SlotContext = createContext<PageSlot>({
  node: null,
  setNode: () => {},
  crumbNode: null,
  setCrumbNode: () => {},
  claims: 0,
  setClaims: () => {},
});

/**
 * A screen still owns its title and its controls; it just renders them in the
 * topbar instead of a header of its own. The landing zones sit above
 * `<Routes>`, so the target nodes travel down by context rather than by prop.
 */
export function TopbarPageSlotProvider({ children }: { children: ReactNode }) {
  const [node, setNode] = useState<HTMLElement | null>(null);
  const [crumbNode, setCrumbNode] = useState<HTMLElement | null>(null);
  const [claims, setClaims] = useState(0);
  const value = useMemo(
    () => ({ node, setNode, crumbNode, setCrumbNode, claims, setClaims }),
    [node, crumbNode, claims],
  );
  return <SlotContext.Provider value={value}>{children}</SlotContext.Provider>;
}

/** Where page controls land. Rendered once, by the topbar. */
export function TopbarPageSlot() {
  const { setNode } = useContext(SlotContext);
  return <div className="topbar-page" ref={setNode} />;
}

/** The rail page the current route belongs to: the breadcrumb's first step. */
function useRailPage() {
  const { pathname } = useLocation();
  const page = pageFromPath(pathname);
  return SIDEBAR_PAGES.find((entry) => entry.ownsPage(page)) ?? null;
}

/**
 * Where you are, as the rail names it, then the page's own title when it says
 * something more ("Initiatives › Release train"). A title that only repeats the
 * page's name is drawn once. The last step is the page's one `<h1>`.
 */
function Crumbs({ title }: { title?: ReactNode }) {
  const page = useRailPage();
  const repeatsPage = title === undefined || (page !== null && title === page.label);
  if (repeatsPage) {
    return <h1 className="topbar-page-title">{title ?? page?.label ?? "loregarden"}</h1>;
  }
  return (
    <>
      {page ? (
        <>
          <Link className="topbar-crumb-link" to={page.path}>
            {page.label}
          </Link>
          <span className="topbar-crumb-sep" aria-hidden="true">
            ›
          </span>
        </>
      ) : null}
      <h1 className="topbar-page-title">{title}</h1>
    </>
  );
}

/**
 * The breadcrumb landmark. It shows the rail page's name on its own until a
 * mounted `PageTopbar` claims it; the claim is a count rather than a flag so a
 * route swap that mounts the next page before unmounting the last stays right.
 */
export function TopbarCrumbs() {
  const { setCrumbNode, claims } = useContext(SlotContext);
  return (
    <nav className="topbar-crumbs" aria-label="Breadcrumb" ref={setCrumbNode}>
      {claims === 0 ? <Crumbs /> : null}
    </nav>
  );
}

/**
 * The screen's title and controls, hoisted into the topbar: the title into the
 * breadcrumb, the controls into the page slot. Renders nothing until the slots
 * exist — the first paint of a route happens before the topbar's nodes attach.
 */
export function PageTopbar({ title, children }: { title: ReactNode; children?: ReactNode }) {
  const { node, crumbNode, setClaims } = useContext(SlotContext);
  useEffect(() => {
    setClaims((claims) => claims + 1);
    return () => setClaims((claims) => claims - 1);
  }, [setClaims]);
  return (
    <>
      {crumbNode ? createPortal(<Crumbs title={title} />, crumbNode) : null}
      {node && children
        ? createPortal(<div className="topbar-page-actions">{children}</div>, node)
        : null}
    </>
  );
}
