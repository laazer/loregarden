import { QueryClientProvider } from "@tanstack/react-query";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppLayout } from "./components/AppLayout";
import { RouterBridgeSync } from "./components/RouterBridgeSync";
import { StudioSectionRedirect } from "./components/StudioSectionRedirect";
import { TicketRouteResolver } from "./components/TicketRouteResolver";
import { TicketTabRedirect } from "./components/TicketTabRedirect";
import { McpGatewayPage } from "./pages/McpGatewayPage";
import { BaxterChatPage } from "./pages/BaxterChatPage";
import { BranchTriagePage } from "./pages/BranchTriagePage";
import { Dashboard } from "./pages/Dashboard";
import { EditorPage } from "./pages/EditorPage";
import { HomePage } from "./pages/HomePage";
import { QueuePage } from "./pages/QueuePage";
import { StudioPage } from "./pages/StudioPage";
import { ViewPage } from "./pages/ViewPage";
import { viewIdFromPath } from "./lib/appNavigation";
import { navigateToPage, pageFromPath } from "./lib/useAppNavigation";
import { createQueryClient } from "./api/queryClient";
import { QueueStatusProvider } from "./state/QueueStatusContext";
import "./index.css";

const queryClient = createQueryClient();

class PageErrorBoundary extends Component<
  { children: ReactNode; resetKey: string },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prevProps: { resetKey: string }) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Page render failed", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="queue-page-empty">
          <h2 style={{ marginTop: 0 }}>This page failed to load</h2>
          <p style={{ maxWidth: 520 }}>{this.state.error.message}</p>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              navigateToPage("home");
              this.setState({ error: null });
            }}
          >
            Back to Home
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function AppShell() {
  const location = useLocation();
  /**
   * One boundary key per destination. `pageFromPath` answers `"home"` for every
   * path it does not recognise, so keying on it alone gives every view route —
   * and the Home page they escape to — a single shared key: a crash in one view
   * stays on screen in the next, and on the page whose job is to be the way out.
   * The view id is appended rather than substituted, so the non-view pages keep
   * the distinct keys they already had.
   */
  const viewId = viewIdFromPath(location.pathname);
  const errorBoundaryKey = viewId ? `view:${viewId}` : pageFromPath(location.pathname);

  return (
    <QueueStatusProvider>
      <AppLayout>
        <PageErrorBoundary resetKey={errorBoundaryKey}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/chat" element={<BaxterChatPage />} />
            <Route path="/console" element={<Dashboard />} />
            <Route path="/tickets/:ticketId" element={<TicketTabRedirect />} />
            <Route
              path="/tickets/:ticketId/:artifactTab"
              element={
                <TicketRouteResolver>
                  <Dashboard />
                </TicketRouteResolver>
              }
            />
            <Route path="/studio" element={<StudioSectionRedirect />} />
            <Route path="/studio/:studioSection/:resourceId/*" element={<StudioPage />} />
            <Route path="/studio/:studioSection/*" element={<StudioPage />} />
            <Route path="/editor/*" element={<EditorPage />} />
            <Route path="/queue/*" element={<QueuePage />} />
            <Route path="/mcp" element={<McpGatewayPage />} />
            <Route path="/branch-triage" element={<BranchTriagePage />} />
            <Route path="/branch-triage/*" element={<BranchTriagePage />} />
            {/* Before the catch-all, which would otherwise bounce every view
                deep-link back to Home. */}
            <Route path="/view/:viewId" element={<ViewPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </PageErrorBoundary>
      </AppLayout>
    </QueueStatusProvider>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <RouterBridgeSync />
        <AppShell />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
