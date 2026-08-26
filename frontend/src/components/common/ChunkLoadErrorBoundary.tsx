import React from 'react';
import { reportFrontendError, logFallback } from '@utils/errorReporter';
import { isChunkLoadError } from './isChunkLoadError';

interface ChunkLoadErrorBoundaryProps {
  children: React.ReactNode;
  reloadPage?: () => void;
}

interface ChunkLoadErrorBoundaryState {
  error: Error | null;
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

  // Instance property to store errorId (avoiding re-render)
  private errorId: string | null = null;

  public static getDerivedStateFromError(error: Error): ChunkLoadErrorBoundaryState {
    return { error };
  }

  public override componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    try {
      // Issue #2953: Build context object with additional information
      const context: Record<string, unknown> = {
        url: window.location.href,
        path: window.location.pathname,
        search: window.location.search,
      };

      // Extract URL parameters relevant to session restoration
      const urlParams = new URLSearchParams(window.location.search);
      const sessionId = urlParams.get('sessionId');
      const encodedProjectName = urlParams.get('encodedProjectName');
      const restoreSession = urlParams.get('restoreSession');

      if (sessionId) context.sessionId = sessionId;
      if (encodedProjectName) context.encodedProjectName = encodedProjectName;
      if (restoreSession) context.restoreSession = restoreSession;

      // Add store state summary if available
      try {
        const storedState = localStorage.getItem('open-ace-store');
        if (storedState) {
          const parsed = JSON.parse(storedState);
          if (parsed?.state) {
            context.storeState = {
              hasTabs: Array.isArray(parsed.state.workspaceTabs),
              tabsCount: Array.isArray(parsed.state.workspaceTabs)
                ? parsed.state.workspaceTabs.length
                : 0,
              hasActiveTabId: typeof parsed.state.workspaceActiveTabId === 'string',
              hasTabsOrder: Array.isArray(parsed.state.workspaceTabsOrder),
              tabsOrderCount: Array.isArray(parsed.state.workspaceTabsOrder)
                ? parsed.state.workspaceTabsOrder.length
                : 0,
            };
          }
        }
      } catch {
        // Ignore errors when reading localStorage
      }

      // Report error with context
      this.errorId = reportFrontendError({
        error,
        errorInfo: { componentStack: errorInfo.componentStack ?? undefined },
        category: isChunkLoadError(error) ? 'chunk-load' : 'render-runtime',
        context,
      });
    } catch (e) {
      // Swallow all exceptions to prevent infinite loop
      logFallback('[ErrorReporter] Failed to report', e);
      this.errorId = 'fallback';
    }
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
    const displayErrorId = this.errorId ?? 'pending';

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
          <p className="text-muted mb-2">
            {chunkFailure
              ? 'This page is still using files from an older version. Reload to continue safely.'
              : 'An unexpected display error occurred. Reload the page to try again.'}
          </p>
          <p className="text-muted small mb-4">
            Error ID: <code className="user-select-all">{displayErrorId}</code>
          </p>
          <button className="btn btn-primary" type="button" onClick={this.reloadPage}>
            Reload page
          </button>
        </section>
      </main>
    );
  }
}
