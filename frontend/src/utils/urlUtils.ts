/**
 * URL utilities for workspace iframe URL construction
 */

/**
 * Build the "/projects/<encoded-segments>" path segment from a raw project path.
 * Each path segment is encodeURIComponent'd so spaces / special chars survive the URL,
 * while slashes are preserved as path separators (matching how
 * ProjectSelector.navigate(`/projects${path}`) works).
 *
 * @param projectPath - Raw project path (e.g., "/home/user/my-project")
 * @returns Encoded path segment (e.g., "projects/home/user/my-project")
 */
export function buildProjectsPathSegment(projectPath: string): string {
  // Filter out empty segments (from leading slash, trailing slash, or double slashes)
  const segments = projectPath.split('/').filter((seg) => seg !== '');
  const encodedSegments = segments.map((seg) => encodeURIComponent(seg));
  return encodedSegments.length > 0 ? `projects/${encodedSegments.join('/')}` : 'projects';
}

/**
 * Inject the projects path segment into a base URL (before any existing query string).
 * e.g., "http://h:3100" → "http://h:3100/projects/home/u"
 *
 * @param base - Base URL to inject path into
 * @param projectsPathSegment - Pre-built projects path segment (e.g., "projects/home/user")
 * @returns URL with projects path injected
 */
export function injectProjectsPath(base: string, projectsPathSegment: string): string {
  if (!projectsPathSegment) return base;
  const qIdx = base.indexOf('?');
  const pathPart = qIdx === -1 ? base : base.slice(0, qIdx);
  const queryPart = qIdx === -1 ? '' : base.slice(qIdx);
  const glue = pathPart.endsWith('/') ? '' : '/';
  return `${pathPart}${glue}${projectsPathSegment}${queryPart}`;
}
