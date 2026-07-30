/**
 * Project path utilities for encoding/decoding project paths.
 *
 * Used for URL-safe encoding of project paths in session management.
 * Supports both new (b64:) and legacy (-home-user-project) formats.
 */

const B64_PREFIX = 'b64:';

/**
 * Decode an encoded project name back to the original path.
 * Supports both new (b64:) and legacy (-home-user-project) formats.
 *
 * @param encodedName - The encoded project name
 * @returns The decoded project path, or empty string if input is empty
 */
export function decodeProjectName(encodedName: string): string {
  if (!encodedName) return '';

  // New format: b64:<base64>
  if (encodedName.startsWith(B64_PREFIX)) {
    try {
      const b64Data = encodedName.slice(B64_PREFIX.length);
      // Add back padding if needed
      const padding = (4 - (b64Data.length % 4)) % 4;
      const paddedData = b64Data + '='.repeat(padding);
      // Use base64 decoding (browser native atob handles URL-safe base64)
      const decoded = decodeURIComponent(
        Array.from(atob(paddedData))
          .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
          .join('')
      );
      return decoded;
    } catch (e) {
      console.warn('Failed to decode project name:', encodedName, e);
      return encodedName;
    }
  }

  // Legacy format: -home-user-project (backward compatible)
  // Convert back: -home-user-demo-project -> /home/user/demo-project
  if (encodedName.startsWith('-')) {
    return '/' + encodedName.slice(1).replace(/-/g, '/');
  }

  // Not encoded, return as-is
  return encodedName;
}

/**
 * Encode a project path to a URL-safe string.
 *
 * @param projectPath - The absolute path to encode (e.g., /home/user/demo-project)
 * @returns Encoded string with b64: prefix
 */
export function encodeProjectPath(projectPath: string): string {
  if (!projectPath) return '';

  // Normalize Windows paths: /C:/Users/... -> C:/Users/...
  let normalizedPath = projectPath;
  if (normalizedPath.startsWith('/') && normalizedPath.length > 2) {
    // Check for Windows path pattern: /X:/...
    if (normalizedPath[2] === ':' && normalizedPath[1].match(/[A-Za-z]/)) {
      normalizedPath = normalizedPath.slice(1); // Remove leading /
    }
  }

  // URL-safe Base64 encoding (no padding)
  const encoded = btoa(unescape(encodeURIComponent(normalizedPath)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');

  return `${B64_PREFIX}${encoded}`;
}