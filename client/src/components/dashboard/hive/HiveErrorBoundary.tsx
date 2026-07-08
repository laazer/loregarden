import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

/** Keeps a Pixi failure from blanking the whole Dashboard. */
export class HiveErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[hive] floor crashed", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        this.props.fallback ?? (
          <div className="hive-panel__idle">
            <div className="hive-panel__idle-title">Hive floor unavailable</div>
            <div className="hive-panel__idle-copy">
              Simulation crashed — other ticket tabs should still work. Reload to retry.
            </div>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
