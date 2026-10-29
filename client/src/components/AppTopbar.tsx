import { AppTopbarActions } from "./AppTopbarActions";
import { TopbarCrumbs, TopbarPageSlot } from "./TopbarPageSlot";
import { TopbarSearch } from "./TopbarSearch";

/**
 * The one header every page gets: the wordmark, where you are, one search box,
 * then the page's own controls and the Inbox. 44px, one row, the same on every
 * page; GitHub sync moved to Settings and to each ticket's header, Instances
 * and Usage to the Machine page.
 */
export function AppTopbar() {
  return (
    <header className="topbar app-topbar">
      <span className="brand-title topbar-wordmark">loregarden</span>
      <TopbarCrumbs />
      <TopbarSearch />
      <TopbarPageSlot />
      <div className="topbar-spacer" />
      <AppTopbarActions />
    </header>
  );
}
