import React from 'react';
import { reportFrontendError, logFallback, generateErrorId } from '@utils/errorReporter';
import { isChunkLoadError } from './isChunkLoadError';
import { diagnoseError, generateContactSupportLink, type ErrorDiagnosis } from './errorDiagnosis';

interface ChunkLoadErrorBoundaryProps {
  children: React.ReactNode;
  reloadPage?: () => void;
}

interface ChunkLoadErrorBoundaryState {
  error: Error | null;
  diagnosis: ErrorDiagnosis | null;
}

/**
 * React.lazy rejects when a content-hashed chunk disappears during a release.
 * Suspense does not handle rejected imports, so this boundary keeps the app
 * recoverable instead of allowing React to unmount the entire root.
 *
 * Issue #3277: Enhanced error categorization and user experience.
 */
export class ChunkLoadErrorBoundary extends React.Component<
  ChunkLoadErrorBoundaryProps,
  ChunkLoadErrorBoundaryState
> {
  public override state: ChunkLoadErrorBoundaryState = { error: null, diagnosis: null };

  // Instance property to store errorId (pre-generated in getDerivedStateFromError)
  private errorId: string = 'pending';

  public static getDerivedStateFromError(error: Error): ChunkLoadErrorBoundaryState {
    // Issue #3277: Pre-generate error ID to avoid showing "pending"
    const errorId = generateErrorId();

    // Store error ID in a way that persists to componentDidCatch
    // We'll use a static map to associate error with its ID
    ChunkLoadErrorBoundary._pendingErrorId = errorId;

    // Diagnose error type for better user guidance
    const diagnosis = diagnoseError(error);

    return { error, diagnosis };
  }

  // Static property to track pending error ID
  private static _pendingErrorId: string = 'pending';

  public override componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    // Use pre-generated error ID from getDerivedStateFromError
    this.errorId = ChunkLoadErrorBoundary._pendingErrorId;

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
      // Use enhanced categorization from diagnosis if available
      const category =
        this.state.diagnosis?.type ?? (isChunkLoadError(error) ? 'chunk-load' : 'render-runtime');

      const reportedId = reportFrontendError({
        error,
        errorInfo: { componentStack: errorInfo.componentStack ?? undefined },
        category,
        context,
      });

      // Use reported ID if available
      if (reportedId) {
        this.errorId = reportedId;
      }
    } catch (e) {
      // Swallow all exceptions to prevent infinite loop
      logFallback('[ErrorReporter] Failed to report', e);
      // Keep pre-generated error ID
    }
  }

  private readonly reloadPage = (): void => {
    if (this.props.reloadPage) {
      this.props.reloadPage();
      return;
    }
    window.location.reload();
  };

  private readonly handleContactSupport = (): void => {
    const { diagnosis } = this.state;
    const errorType = diagnosis?.type ?? 'render-runtime';

    const mailtoLink = generateContactSupportLink({
      errorType,
      errorId: this.errorId,
      pathname: window.location.pathname,
      timestamp: new Date().toISOString(),
    });

    window.location.href = mailtoLink;
  };

  public override render(): React.ReactNode {
    const { error, diagnosis } = this.state;
    if (!error) return this.props.children;

    // Use diagnosis if available, otherwise fall back to basic check
    const title =
      diagnosis?.title ?? (isChunkLoadError(error) ? 'System Updated' : 'Page Render Error');
    const description =
      diagnosis?.description ?? 'An unexpected error occurred. Please reload the page.';
    const showContactSupport = diagnosis?.showContactSupport ?? true;
    const showRetry = diagnosis?.showRetry ?? false;

    return (
      <main
        className="min-vh-100 d-flex align-items-center justify-content-center bg-light p-4"
        data-testid="application-error-boundary"
      >
        <section
          className="card shadow-sm border-0 text-center p-4"
          role="alert"
          style={{ maxWidth: '500px' }}
        >
          <i className="bi bi-exclamation-triangle-fill text-warning fs-1 mb-3" />
          <h1 className="h4 mb-3">{title}</h1>
          <p className="text-muted mb-2">{description}</p>
          <p className="text-muted small mb-4">
            Error ID: <code className="user-select-all">{this.errorId}</code>
          </p>
          <div className="d-flex gap-2 justify-content-center">
            <button className="btn btn-primary" type="button" onClick={this.reloadPage}>
              Reload page
            </button>
            {showRetry && (
              <button className="btn btn-outline-secondary" type="button" onClick={this.reloadPage}>
                Retry
              </button>
            )}
            {showContactSupport && (
              <button
                className="btn btn-outline-primary"
                type="button"
                onClick={this.handleContactSupport}
              >
                <i className="bi bi-envelope me-1" />
                Contact Support
              </button>
            )}
          </div>
        </section>
      </main>
    );
  }
}
