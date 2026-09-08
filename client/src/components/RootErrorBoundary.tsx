import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * The boundary above everything, including the chrome.
 *
 * `PageErrorBoundary` sits *inside* `AppLayout`, so it can only catch what the
 * routed page throws. A throw in the layout itself — the sidebar, the topbar,
 * the dock, the queue provider, or the toast host the other failures are
 * reported through — unmounts the whole tree past this point and leaves a blank
 * white document with nothing to click.
 *
 * Deliberately self-contained: no toast, no store, no router. Everything this
 * would otherwise lean on is a thing that might be what just crashed, and a
 * fallback that throws is worse than the blank page it replaces. Inline styles
 * for the same reason — the stylesheet is not a given here.
 */
export class RootErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("App shell crashed", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          gap: 12,
          padding: 32,
          fontFamily: "system-ui, sans-serif",
          maxWidth: 640,
        }}
      >
        <h1 style={{ margin: 0, fontSize: 18 }}>The app failed to start</h1>
        <p style={{ margin: 0, opacity: 0.8 }}>{this.state.error.message}</p>
        <button type="button" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
