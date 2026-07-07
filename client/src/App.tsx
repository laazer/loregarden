import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "./components/AppLayout";
import { RouterBridgeSync } from "./components/RouterBridgeSync";
import { StudioSectionRedirect } from "./components/StudioSectionRedirect";
import { TicketTabRedirect } from "./components/TicketTabRedirect";
import { Dashboard } from "./pages/Dashboard";
import { EditorPage } from "./pages/EditorPage";
import { QueuePage } from "./pages/QueuePage";
import { StudioPage } from "./pages/StudioPage";
import { navigateToPage } from "./lib/useAppNavigation";
import "./index.css";

const queryClient = new QueryClient();

class PageErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
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
              navigateToPage("dashboard");
              this.setState({ error: null });
            }}
          >
            Back to IDE
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function AppShell() {
  return (
    <AppLayout>
      <PageErrorBoundary>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/tickets/:ticketId" element={<TicketTabRedirect />} />
          <Route path="/tickets/:ticketId/:artifactTab" element={<Dashboard />} />
          <Route path="/studio" element={<StudioSectionRedirect />} />
          <Route path="/studio/:studioSection/:resourceId/*" element={<StudioPage />} />
          <Route path="/studio/:studioSection/*" element={<StudioPage />} />
          <Route path="/editor/*" element={<EditorPage />} />
          <Route path="/queue/*" element={<QueuePage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </PageErrorBoundary>
    </AppLayout>
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
