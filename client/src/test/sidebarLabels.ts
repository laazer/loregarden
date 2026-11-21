import { SIDEBAR_GROUPS, SIDEBAR_PAGES } from "../components/appSidebarPages";

/**
 * The built-in pages' names in the order the rail draws them: group by group
 * (Work, Build, System), declaration order within each. Derived, so adding a
 * page to `SIDEBAR_PAGES` does not also mean editing every test that lists the
 * rail — the tests still prove each page is drawn, once, in its group's place.
 */
export const SIDEBAR_LABELS_IN_RAIL_ORDER: readonly string[] = SIDEBAR_GROUPS.flatMap((group) =>
  SIDEBAR_PAGES.filter((page) => page.group === group.key).map((page) => page.label),
);
