/**
 * Browser-download helper.
 *
 * Triggers a "Save As" for a Blob by creating a temporary anchor element.
 * Used by the personal-files download action. Mirrors the inline pattern at
 * ComplianceMgmt.tsx but extracted so multiple callers share one impl.
 */

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } finally {
    // Revoke on the next tick so the click has a chance to resolve.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
