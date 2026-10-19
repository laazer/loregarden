import { useEffect, useRef } from "react";

import type { TicketTreeNode } from "../api/types";
import { collectExpandableIds } from "../components/TicketTree";

/**
 * Open the searched tree down to its matches, once per search term.
 *
 * The server answers a search with the matches anchored under their
 * ancestors, so a ticket three levels down (an initiative, its milestone, a
 * feature, the match) arrived collapsed under its root: searching `1239` or a
 * full id showed one closed initiative and read as "no results". Everything in
 * that tree is a match or the path to one, so all of it is opened.
 *
 * Once per term, not on every render: the tree refetches every few seconds,
 * and a node the operator closes during the search stays closed.
 *
 * `tree` is the searched query's own data, `undefined` while it loads, so a
 * term is not marked done against the previous term's tree or an empty one.
 */
export function useExpandSearchMatches(
  search: string,
  tree: TicketTreeNode[] | undefined,
  expandPath: (ids: string[]) => void,
): void {
  const expandedFor = useRef("");

  useEffect(() => {
    const term = search.trim();
    if (!term) {
      expandedFor.current = "";
      return;
    }
    if (tree === undefined || expandedFor.current === term) return;
    const ids = collectExpandableIds(tree);
    if (ids.length) expandPath(ids);
    expandedFor.current = term;
  }, [search, tree, expandPath]);
}
