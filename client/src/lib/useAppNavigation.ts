import { useLocation, useParams } from "react-router-dom";

import { getRouterNavigate } from "./routerBridge";
import {
  pageFromPath,
  pathForPage,
  ticketPath,
  type AppPage,
} from "./appNavigation";

export type { AppPage } from "./appNavigation";
export { pageFromPath, pathForPage, ticketPath, ticketIdFromPath } from "./appNavigation";

export function navigateToPage(page: AppPage, replace = false) {
  const navigate = getRouterNavigate();
  if (!navigate) return;
  navigate(pathForPage(page), { replace });
}

export function navigateToTicket(ticketId: string, replace = false) {
  const navigate = getRouterNavigate();
  if (!navigate) return;
  navigate(ticketPath(ticketId), { replace });
}

export function useAppPage(): AppPage {
  const { pathname } = useLocation();
  return pageFromPath(pathname);
}

export function useTicketIdFromRoute(): string | null {
  const { ticketId } = useParams<{ ticketId?: string }>();
  return ticketId ?? null;
}

export function useAppNavigation() {
  const appPage = useAppPage();
  const ticketId = useTicketIdFromRoute();
  return {
    appPage,
    ticketId,
    navigateToPage,
    navigateToTicket,
  };
}
