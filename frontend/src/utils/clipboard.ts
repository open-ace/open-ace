/**
 * Clipboard Utility - Cross-environment copy to clipboard
 *
 * Provides a robust copy function that works in both secure contexts
 * (HTTPS/localhost) and non-secure contexts (HTTP).
 */

/**
 * Check if the device is an iOS device (including iPad in desktop mode)
 *
 * Note: iPadOS 13+ requests desktop website by default, so userAgent
 * doesn't contain "iPad". We need to check for MacIntel with touch support.
 */
function isIOSDevice(): boolean {
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

/**
 * Copy text to clipboard with fallback for HTTP environments
 *
 * @param text - The text to copy
 * @returns Promise<boolean> - true if copy succeeded, false otherwise
 *
 * Strategy:
 * 1. First try modern clipboard API (requires HTTPS or localhost)
 * 2. If failed or unavailable, fallback to execCommand with textarea
 *
 * Note: execCommand("copy") is deprecated but still needed as fallback
 * for HTTP environments until a better cross-browser solution is available.
 * See: https://developer.mozilla.org/en-US/docs/Web/API/Document/execCommand
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  // Early return for empty/invalid content
  if (!text || typeof text !== "string") {
    console.warn("[clipboard] Invalid input: empty or non-string");
    return false;
  }

  // Try modern clipboard API first (requires secure context)
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      console.debug("[clipboard] Copy succeeded via clipboard API");
      return true;
    }
  } catch (err) {
    console.warn("[clipboard] Clipboard API failed, using execCommand fallback:", err);
  }

  // Fallback: use execCommand with a temporary textarea element
  // This works in HTTP environments where clipboard API is blocked
  // Note: execCommand is deprecated but necessary for HTTP environments
  const textarea = document.createElement("textarea");
  textarea.value = text;

  // Make textarea invisible but still functional
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  textarea.style.width = "2em";
  textarea.style.height = "2em";
  textarea.style.padding = "0";
  textarea.style.border = "none";
  textarea.style.outline = "none";
  textarea.style.boxShadow = "none";
  textarea.style.background = "transparent";
  // Ensure text can be selected (important for some browsers)
  textarea.style.userSelect = "text";
  textarea.style.webkitUserSelect = "text";
  textarea.setAttribute("readonly", ""); // Prevent mobile keyboard popup

  document.body.appendChild(textarea);

  // Select the text
  let success = false;
  try {
    // iOS-specific handling (including iPad in desktop mode)
    if (isIOSDevice()) {
      const range = document.createRange();
      range.selectNodeContents(textarea);
      const selection = window.getSelection();
      if (selection) {
        selection.removeAllRanges();
        selection.addRange(range);
      }
      textarea.setSelectionRange(0, text.length);
    } else {
      textarea.focus();
      textarea.select();
    }

    success = document.execCommand("copy");
    if (success) {
      console.debug("[clipboard] Copy succeeded via execCommand fallback");
    } else {
      console.warn("[clipboard] execCommand returned false");
    }
  } catch (err) {
    console.warn("[clipboard] execCommand failed:", err);
    success = false;
  }

  // Clean up - ensure textarea is removed even on error
  try {
    document.body.removeChild(textarea);
  } catch {
    // Element may have already been removed
  }

  return success;
}
