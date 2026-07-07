import { useLocation, useParams } from "react-router-dom";

import { getRouterNavigate } from "./routerBridge";
import {
  artifactTabFromPath,
  isArtifactTab,
  isStudioSection,
  pageFromPath,
  pathForPage,
  studioAgentNewPath,
  studioAgentPath,
  studioPath,
  studioResourceFromPath,
  studioResourcePath,
  studioSectionFromPath,
  studioTicketSessionNewPath,
  studioTicketSessionPath,
  studioWorkflowNewPath,
  studioWorkflowPath,
  ticketPath,
  type AppPage,
  type ArtifactTab,
  type StudioSection,
} from "./appNavigation";

export type { AppPage, ArtifactTab, StudioSection } from "./appNavigation";
export {
  artifactTabFromPath,
  isArtifactTab,
  isStudioNewResource,
  isStudioSection,
  pageFromPath,
  pathForPage,
  studioAgentNewPath,
  studioAgentPath,
  studioPath,
  studioResourceFromPath,
  studioResourcePath,
  studioSectionFromPath,
  studioTicketSessionNewPath,
  studioTicketSessionPath,
  studioWorkflowNewPath,
  studioWorkflowPath,
  ticketIdFromPath,
  ticketPath,
} from "./appNavigation";

export function navigateToPage(page: AppPage, replace = false) {
  const navigate = getRouterNavigate();
  if (!navigate) return;
  navigate(pathForPage(page), { replace });
}

export function navigateToStudio(section: StudioSection = "agents", replace = false) {
  const navigate = getRouterNavigate();
  if (!navigate) return;
  navigate(studioPath(section), { replace });
}

export function navigateToStudioResource(
  section: StudioSection,
  resourceId: string,
  replace = false,
) {
  const navigate = getRouterNavigate();
  if (!navigate) return;
  navigate(studioResourcePath(section, resourceId), { replace });
}

export function navigateToStudioAgent(slug: string, replace = false) {
  navigateToStudioResource("agents", slug, replace);
}

export function navigateToStudioAgentNew(replace = false) {
  navigateToStudioResource("agents", "new", replace);
}

export function navigateToStudioWorkflow(slug: string, replace = false) {
  navigateToStudioResource("workflows", slug, replace);
}

export function navigateToStudioWorkflowNew(replace = false) {
  navigateToStudioResource("workflows", "new", replace);
}

export function navigateToStudioTicketSession(sessionId: string, replace = false) {
  navigateToStudioResource("tickets", sessionId, replace);
}

export function navigateToStudioTicketSessionNew(replace = false) {
  navigateToStudioResource("tickets", "new", replace);
}

export function navigateToTicket(
  ticketId: string,
  options?: { tab?: ArtifactTab; replace?: boolean },
) {
  const navigate = getRouterNavigate();
  if (!navigate) return;
  navigate(ticketPath(ticketId, options?.tab ?? "diff"), { replace: options?.replace ?? false });
}

export function navigateToTicketTab(ticketId: string, tab: ArtifactTab, replace = false) {
  navigateToTicket(ticketId, { tab, replace });
}

export function useAppPage(): AppPage {
  const { pathname } = useLocation();
  return pageFromPath(pathname);
}

export function useTicketIdFromRoute(): string | null {
  const { ticketId } = useParams<{ ticketId?: string }>();
  return ticketId ?? null;
}

export function useArtifactTabFromRoute(defaultTab: ArtifactTab = "diff"): ArtifactTab {
  const { pathname } = useLocation();
  const { artifactTab } = useParams<{ artifactTab?: string }>();
  if (artifactTab && isArtifactTab(artifactTab)) return artifactTab;
  return artifactTabFromPath(pathname) ?? defaultTab;
}

export function useStudioSectionFromRoute(): StudioSection {
  const { pathname } = useLocation();
  const { studioSection } = useParams<{ studioSection?: string }>();
  if (studioSection && isStudioSection(studioSection)) return studioSection;
  return studioSectionFromPath(pathname);
}

export function useStudioResourceFromRoute(): string | null {
  const { pathname } = useLocation();
  const { resourceId } = useParams<{ resourceId?: string }>();
  return resourceId ?? studioResourceFromPath(pathname);
}

export function useAppNavigation() {
  const appPage = useAppPage();
  const ticketId = useTicketIdFromRoute();
  const artifactTab = useArtifactTabFromRoute();
  const studioSection = useStudioSectionFromRoute();
  const studioResourceId = useStudioResourceFromRoute();
  return {
    appPage,
    ticketId,
    artifactTab,
    studioSection,
    studioResourceId,
    navigateToPage,
    navigateToStudio,
    navigateToStudioAgent,
    navigateToStudioAgentNew,
    navigateToStudioWorkflow,
    navigateToStudioWorkflowNew,
    navigateToStudioTicketSession,
    navigateToStudioTicketSessionNew,
    navigateToTicket,
    navigateToTicketTab,
  };
}
