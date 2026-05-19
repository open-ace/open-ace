/**
 * Clipboard Utility - Cross-environment copy to clipboard
 */

export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text || typeof text !== 'string') {
    return false;
  }

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Clipboard API failed, proceed to fallback
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.top = '0';
  textarea.style.width = '2em';
  textarea.style.height = '2em';
  textarea.style.padding = '0';
  textarea.style.border = 'none';
  textarea.style.outline = 'none';
  textarea.style.boxShadow = 'none';
  textarea.style.background = 'transparent';
  textarea.setAttribute('readonly', '');

  document.body.appendChild(textarea);

  let success = false;
  try {
    const isIOS = navigator.userAgent.match(/ipad|iphone/i);
    if (isIOS) {
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
    success = document.execCommand('copy');
  } catch {
    success = false;
  }

  document.body.removeChild(textarea);
  return success;
}
