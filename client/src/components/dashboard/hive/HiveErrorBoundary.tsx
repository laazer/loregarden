import { Component, type ErrorInfo, type ReactNode } from "react";

import { toastActionFailed } from "../../../state/toastStore";

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
    // The fallback replaces one panel on a page full of them, and reads a lot
    // like the ordinary "quiet floor" state; the toast is what distinguishes a
    // crash from an idle ticket.
    toastActionFailed("Hive floor", error);
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
