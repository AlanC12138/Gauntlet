import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Keeps a rendering failure local.
 *
 * A run costs real money and several minutes, and its report is already safe in
 * Postgres by the time it reaches the browser. Letting one bad value in the
 * results panel blank the whole page would make a recoverable display problem
 * look like a lost run — so the failure is caught and the rest of the app,
 * including the run history, stays usable.
 */
interface Props {
  children: ReactNode;
  label: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`${this.props.label} failed to render`, error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="error">
          <strong>{this.props.label} could not be displayed.</strong>{" "}
          {this.state.error.message}
          <p className="muted">
            The run itself is unaffected — its report is stored and can be fetched
            from the API directly.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
