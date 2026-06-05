/**
 * ContextMenu Component - Global right-click context menu
 *
 * Replaces browser's native right-click menu with custom business menu.
 * Implements smart menu items based on context:
 * - On links: Copy link, Open in new tab
 * - On text selection: Copy selected text
 * - On blank area: Refresh page
 *
 * Features:
 * - Keyboard navigation (Arrow keys + Enter)
 * - Clipboard API error handling with fallback
 * - Input/Textarea whitelist for native menu
 * - Toast feedback for copy operations
 */

import React, { useState, useEffect, useCallback, useRef, createContext, useContext } from 'react';
import { cn } from '@/utils';
import { useAppStore } from '@/store';
import { t } from '@/i18n';
import { useToast } from './Toast';

// Context menu state
interface ContextMenuState {
  isOpen: boolean;
  x: number;
  y: number;
  targetElement: HTMLElement | null;
  linkUrl: string | null;
  selectedText: string | null;
}

interface ContextMenuContextType {
  state: ContextMenuState;
  showMenu: (x: number, y: number, target: HTMLElement) => void;
  hideMenu: () => void;
  showToast: {
    success: (title: string, message?: string) => void;
    error: (title: string, message?: string) => void;
  };
}

const ContextMenuContext = createContext<ContextMenuContextType | null>(null);

export const useContextMenu = () => {
  const context = useContext(ContextMenuContext);
  if (!context) {
    throw new Error('useContextMenu must be used within ContextMenuProvider');
  }
  return context;
};

// Menu item interface
interface MenuItem {
  id: string;
  label?: string; // Optional for divider items
  icon?: React.ReactNode;
  disabled?: boolean;
  divider?: boolean;
  danger?: boolean;
  onClick?: () => void;
  visible?: boolean;
}

// Icons
const LinkIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
  </svg>
);

const CopyIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
);

const RefreshIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="23 4 23 10 17 10" />
    <polyline points="1 20 1 14 7 14" />
    <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15" />
  </svg>
);

const NewTabIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    <polyline points="15 3 21 3 21 9" />
    <line x1="10" y1="14" x2="21" y2="3" />
  </svg>
);

/**
 * Copy text to clipboard with error handling and fallback
 */
const copyToClipboard = async (text: string): Promise<boolean> => {
  try {
    // Try modern clipboard API first
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    // Fallback for non-secure contexts or older browsers
    console.warn('Clipboard API failed, using fallback:', err);
    try {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.style.position = 'fixed';
      textarea.style.left = '-9999px';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      return true;
    } catch (fallbackErr) {
      console.error('Failed to copy text:', fallbackErr);
      return false;
    }
  }
};

/**
 * Check if target element should use native context menu
 * (input fields, textareas, etc.)
 */
export const shouldUseNativeMenu = (target: HTMLElement): boolean => {
  const tagName = target.tagName.toLowerCase();
  // Allow native menu for input and textarea elements
  if (tagName === 'input' || tagName === 'textarea') {
    return true;
  }
  // Allow native menu for editable elements
  if (target.isContentEditable) {
    return true;
  }
  return false;
};

/**
 * ContextMenuProvider - Provides global context menu state
 */
export const ContextMenuProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, setState] = useState<ContextMenuState>({
    isOpen: false,
    x: 0,
    y: 0,
    targetElement: null,
    linkUrl: null,
    selectedText: null,
  });

  const { success, error, ToastContainer } = useToast();

  const showMenu = useCallback((x: number, y: number, target: HTMLElement) => {
    // Detect context
    const linkUrl = target.closest('a')?.href || null;
    const selectedText = window.getSelection()?.toString() || null;

    setState({
      isOpen: true,
      x,
      y,
      targetElement: target,
      linkUrl,
      selectedText: selectedText && selectedText.length > 0 ? selectedText : null,
    });
  }, []);

  const hideMenu = useCallback(() => {
    setState((prev) => ({ ...prev, isOpen: false }));
  }, []);

  const showToast = { success, error };

  return (
    <ContextMenuContext.Provider value={{ state, showMenu, hideMenu, showToast }}>
      {children}
      <ContextMenuMenu />
      <ToastContainer />
    </ContextMenuContext.Provider>
  );
};

/**
 * ContextMenuMenu - The actual menu component rendered at click position
 * Supports keyboard navigation (Arrow keys + Enter + Escape)
 */
const ContextMenuMenu: React.FC = () => {
  const { state, hideMenu, showToast } = useContextMenu();
  const menuRef = useRef<HTMLDivElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const language = useAppStore((state) => state.language);

  // Close on click outside and handle keyboard navigation
  useEffect(() => {
    if (!state.isOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        hideMenu();
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        hideMenu();
        return;
      }

      // Get all focusable menu items (excluding dividers)
      const menuItems = menuRef.current?.querySelectorAll('.context-menu-item:not(.disabled)');
      if (!menuItems || menuItems.length === 0) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setFocusedIndex((prev) => {
          const next = prev + 1;
          return next >= menuItems.length ? 0 : next;
        });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setFocusedIndex((prev) => {
          const next = prev - 1;
          return next < 0 ? menuItems.length - 1 : next;
        });
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        const focusedItem = menuItems[focusedIndex] as HTMLButtonElement;
        focusedItem?.click();
      }
    };

    // Delay to avoid immediate close from the same click
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleKeyDown);
    }, 0);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [state.isOpen, hideMenu, focusedIndex]);

  // Reset focused index when menu opens
  useEffect(() => {
    if (state.isOpen) {
      setFocusedIndex(0);
    }
  }, [state.isOpen]);

  // Adjust position to stay within viewport
  const adjustedPosition = useCallback(() => {
    if (!menuRef.current) return { x: state.x, y: state.y };

    const menuWidth = menuRef.current.offsetWidth || 180;
    const menuHeight = menuRef.current.offsetHeight || 200;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let x = state.x;
    let y = state.y;

    if (x + menuWidth > viewportWidth - 10) {
      x = viewportWidth - menuWidth - 10;
    }
    if (y + menuHeight > viewportHeight - 10) {
      y = viewportHeight - menuHeight - 10;
    }

    return { x, y };
  }, [state.x, state.y]);

  // Build menu items based on context
  const menuItems: MenuItem[] = [
    // Link context items
    {
      id: 'open-new-tab',
      label: t('openInNewTab', language),
      icon: <NewTabIcon />,
      visible: !!state.linkUrl,
      onClick: async () => {
        if (state.linkUrl) {
          window.open(state.linkUrl, '_blank', 'noopener,noreferrer');
        }
        hideMenu();
      },
    },
    {
      id: 'copy-link',
      label: t('copyLink', language),
      icon: <LinkIcon />,
      visible: !!state.linkUrl,
      onClick: async () => {
        if (state.linkUrl) {
          const success = await copyToClipboard(state.linkUrl);
          if (success) {
            showToast.success(t('copySuccess', language));
          } else {
            showToast.error(t('copyFailed', language));
          }
        }
        hideMenu();
      },
    },
    // Divider for link items
    {
      id: 'divider-link',
      divider: true,
      visible: !!state.linkUrl,
    },
    // Text selection items
    {
      id: 'copy-text',
      label: t('copySelectedText', language),
      icon: <CopyIcon />,
      visible: !!state.selectedText,
      onClick: async () => {
        if (state.selectedText) {
          const success = await copyToClipboard(state.selectedText);
          if (success) {
            showToast.success(t('copySuccess', language));
          } else {
            showToast.error(t('copyFailed', language));
          }
        }
        hideMenu();
      },
    },
    // Divider for text items
    {
      id: 'divider-text',
      divider: true,
      visible: !!state.selectedText,
    },
    // General items (always visible when no specific context)
    {
      id: 'refresh',
      label: t('refresh', language),
      icon: <RefreshIcon />,
      visible: !state.linkUrl && !state.selectedText,
      onClick: () => {
        window.location.reload();
        hideMenu();
      },
    },
  ];

  const visibleItems = menuItems.filter((item) => item.visible !== false);

  if (!state.isOpen || visibleItems.length === 0) {
    return null;
  }

  const position = adjustedPosition();

  // Track actionable item index for keyboard focus
  let actionableIndex = 0;

  return (
    <div
      ref={menuRef}
      className={cn('context-menu', 'animate-fade-in')}
      style={{
        position: 'fixed',
        left: position.x,
        top: position.y,
        zIndex: 9999,
      }}
      role="menu"
      aria-label="Context menu"
    >
      {visibleItems.map((item) => {
        if (item.divider) {
          return <hr key={item.id} className="context-menu-divider" />;
        }

        const currentIndex = actionableIndex;
        actionableIndex++;
        const isFocused = currentIndex === focusedIndex;

        return (
          <button
            key={item.id}
            type="button"
            className={cn(
              'context-menu-item',
              item.disabled && 'disabled',
              item.danger && 'text-danger',
              isFocused && 'focused'
            )}
            onClick={item.onClick}
            disabled={item.disabled}
            role="menuitem"
            tabIndex={isFocused ? 0 : -1}
          >
            {item.icon && <span className="context-menu-icon">{item.icon}</span>}
            <span className="context-menu-label">{item.label}</span>
          </button>
        );
      })}
    </div>
  );
};

export default ContextMenuMenu;
