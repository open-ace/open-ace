/**
 * Check if error is a chunk load error
 *
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
