import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Component, type ErrorInfo, type ReactNode, useEffect } from "react";
import { Dashboard } from "./pages/Dashboard";
import { EditorPage } from "./pages/EditorPage";
import { QueuePage } from "./pages/QueuePage";
import { StudioPage } from "./pages/StudioPage";
import { pageFromPath } from "./lib/appNavigation";
import { useUiStore } from "./state/uiStore";
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
              useUiStore.getState().setAppPage("dashboard");
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
  const appPage = useUiStore((s) => s.appPage);

  useEffect(() => {
    const syncFromUrl = () => {
      const page = pageFromPath(window.location.pathname);
      if (useUiStore.getState().appPage !== page) {
        useUiStore.setState({ appPage: page });
      }
    };

    window.addEventListener("popstate", syncFromUrl);
    return () => window.removeEventListener("popstate", syncFromUrl);
  }, []);

  const page =
    appPage === "studio" ? (
      <StudioPage />
    ) : appPage === "editor" ? (
      <EditorPage />
    ) : appPage === "queue" ? (
      <QueuePage />
    ) : (
      <Dashboard />
    );

  return <PageErrorBoundary>{page}</PageErrorBoundary>;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell />
    </QueryClientProvider>
  );
}
