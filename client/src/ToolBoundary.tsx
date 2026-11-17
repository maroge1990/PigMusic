import { Component, type ReactNode } from "react";

/** Keep collection navigation usable when an old tab meets a new asset release. */
export class ToolBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed)
      return (
        <section className="system-panel" role="alert">
          <p>Couldn't load these tools. Reload to open the current version.</p>
          <button
            className="secondary-button"
            onClick={() => window.location.reload()}
          >
            Reload
          </button>
        </section>
      );
    return this.props.children;
  }
}
