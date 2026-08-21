/**
 * One pane's crash stays in that pane.
 *
 * A view renders N containers in a single React tree (440's grid, 442's
 * canvas), so an exception thrown by any primitive — an xterm that cannot
 * attach, a ledger row the panel cannot read — unmounts the whole tree and
 * blanks every other pane with it. React only stops that at an error boundary,
 * and a boundary only helps where it wraps: this one lives inside
 * `ContainerPrimitiveHost`, so every consumer of the host inherits it and no
 * view kind has to remember to add its own.
 *
 * Same pattern as `dashboard/hive/HiveErrorBoundary`; the copy is the
 * container's, and the reset key is the container id so re-pointing a pane at a
 * different container retries instead of staying dead.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  /** Changing this clears a caught error — a different container, a fresh try. */
  resetKey: string;
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
  resetKey: string;
}

export class PrimitiveErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey !== state.resetKey) return { error: null, resetKey: props.resetKey };
    return null;
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[views] container primitive crashed", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        this.props.fallback ?? (
          <p
            data-primitive-crashed="true"
            style={{ margin: 0, padding: 16, color: "var(--txl)", fontSize: 12.5 }}
          >
            This pane stopped working. Other panes in this view are unaffected.
          </p>
        )
      );
    }
    return this.props.children;
  }
}
