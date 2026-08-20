import React from 'react';

interface ChunkLoadErrorBoundaryProps {
  children: React.ReactNode;
  reloadPage?: () => void;
}

interface ChunkLoadErrorBoundaryState {
  error: Error | null;
}

/**
 * Return true only for browser errors that specifically identify a failed
 * JavaScript chunk or dynamic import. Generic network errors must not turn
 * ordinary API failures into full-page reload prompts.
 */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  if (error.name === 'ChunkLoadError') return true;
  return [
    /Loading (?:CSS )?chunk [^ ]+ failed/i,
    /Failed to fetch dynamically imported module/i,
    /Importing a module script failed/i,
    /error loading dynamically imported module/i,
  ].some((pattern) => pattern.test(error.message));
}

/**
 * React.lazy rejects when a content-hashed chunk disappears during a release.
 * Suspense does not handle rejected imports, so this boundary keeps the app
 * recoverable instead of allowing React to unmount the entire root.
 */
export class ChunkLoadErrorBoundary extends React.Component<
  ChunkLoadErrorBoundaryProps,
  ChunkLoadErrorBoundaryState
> {
  public override state: ChunkLoadErrorBoundaryState = { error: null };

  public static getDerivedStateFromError(error: Error): ChunkLoadErrorBoundaryState {
    return { error };
  }

  private readonly reloadPage = (): void => {
    if (this.props.reloadPage) {
      this.props.reloadPage();
      return;
    }
    window.location.reload();
  };

  public override render(): React.ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    const chunkFailure = isChunkLoadError(error);
    return (
      <main
        className="min-vh-100 d-flex align-items-center justify-content-center bg-light p-4"
        data-testid="application-error-boundary"
      >
        <section className="card shadow-sm border-0 text-center p-4" role="alert">
          <i className="bi bi-exclamation-triangle-fill text-warning fs-1 mb-3" />
          <h1 className="h4">
            {chunkFailure ? 'A new version of Open ACE is available' : 'Open ACE could not render'}
          </h1>
          <p className="text-muted mb-4">
            {chunkFailure
              ? 'This page is still using files from an older version. Reload to continue safely.'
              : 'An unexpected display error occurred. Reload the page to try again.'}
          </p>
          <button className="btn btn-primary" type="button" onClick={this.reloadPage}>
            Reload page
          </button>
        </section>
      </main>
    );
  }
}
