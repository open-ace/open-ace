/**
 * Enhanced Error Diagnosis
 *
 * Classifies errors into specific types for better user guidance.
 * Issue #3277: Improved error categorization and user experience.
 */

export type ErrorType =
  | 'build-artifact-missing' // Frontend build artifacts are missing
  | 'chunk-404' // Specific chunk file 404
  | 'chunk-load-failed' // Chunk load failed (network or other reason)
  | 'render-runtime'; // JavaScript runtime error

export interface ErrorDiagnosis {
  type: ErrorType;
  title: string;
  description: string;
  showContactSupport: boolean;
  showRetry: boolean;
}

/**
 * Diagnose error based on error message and properties.
 *
 * Priority: Error message parsing > Network checks (avoid CORS issues)
 */
export function diagnoseError(error: Error): ErrorDiagnosis {
  const message = error.message.toLowerCase();
  const name = error.name.toLowerCase();

  // 1. Check for chunk load errors
  if (name === 'chunkloaderror' || isChunkLoadErrorPattern(message)) {
    // Distinguish between 404 and other failures
    if (message.includes('404') || message.includes('not found')) {
      return {
        type: 'chunk-404',
        title: 'System Updated',
        description:
          'A new version is available. Please reload the page to load the latest version.',
        showContactSupport: false,
        showRetry: false,
      };
    }

    // Check for network-related failures
    if (
      message.includes('network') ||
      message.includes('failed to fetch') ||
      message.includes('net::err') ||
      message.includes('timeout')
    ) {
      return {
        type: 'chunk-load-failed',
        title: 'Resource Load Failed',
        description:
          'Network connection issue or resource unavailable. Please check your network connection and try again.',
        showContactSupport: true,
        showRetry: true,
      };
    }

    // Generic chunk load failure
    return {
      type: 'chunk-load-failed',
      title: 'Resource Load Failed',
      description: 'Failed to load required resources. Please try reloading the page.',
      showContactSupport: true,
      showRetry: true,
    };
  }

  // 2. Check for JavaScript runtime errors
  if (
    name === 'typeerror' ||
    name === 'referenceerror' ||
    name === 'syntaxerror' ||
    name === 'rangeerror'
  ) {
    return {
      type: 'render-runtime',
      title: 'Page Render Error',
      description: 'An unexpected error occurred while rendering the page. The error has been logged.',
      showContactSupport: true,
      showRetry: false,
    };
  }

  // 3. Default to render-runtime for unknown errors
  return {
    type: 'render-runtime',
    title: 'Page Render Error',
    description: 'An unexpected error occurred. The error has been logged.',
    showContactSupport: true,
    showRetry: false,
  };
}

/**
 * Check if error message matches chunk load error patterns.
 */
function isChunkLoadErrorPattern(message: string): boolean {
  const patterns = [
    /loading (?:css )?chunk [^ ]+ failed/i,
    /failed to fetch dynamically imported module/i,
    /importing a module script failed/i,
    /error loading dynamically imported module/i,
    /chunk load error/i,
    /loading chunk.*timeout/i,
  ];

  return patterns.some((pattern) => pattern.test(message));
}

/**
 * Get build version for error reporting.
 * Uses build-time injected constants if available.
 */
export function getBuildVersion(): string {
  // These are injected by vite.config.ts at build time
  if (typeof __BUILD_VERSION__ !== 'undefined') {
    return __BUILD_VERSION__;
  }
  return 'unknown';
}

/**
 * Get commit SHA for error reporting.
 * Uses build-time injected constants if available.
 */
export function getCommitSha(): string {
  // These are injected by vite.config.ts at build time
  if (typeof __COMMIT_SHA__ !== 'undefined') {
    return __COMMIT_SHA__;
  }
  return 'unknown';
}

/**
 * Generate mailto link for contacting support.
 */
export function generateContactSupportLink(params: {
  errorType: ErrorType;
  errorId: string;
  pathname: string;
  timestamp: string;
}): string {
  const { errorType, errorId, pathname, timestamp } = params;

  const subject = encodeURIComponent(`Open ACE Error Report - Error ID: ${errorId}`);

  const body = encodeURIComponent(
    [
      'Error Type: ' + errorType,
      'Error ID: ' + errorId,
      'Page Path: ' + pathname,
      'Time: ' + timestamp,
      '',
      'Please describe the issue you encountered:',
      '',
      '',
      '---',
      'Technical Information:',
      'Build Version: ' + getBuildVersion(),
      'Commit: ' + getCommitSha(),
    ].join('\n')
  );

  return `mailto:support@openace.io?subject=${subject}&body=${body}`;
}