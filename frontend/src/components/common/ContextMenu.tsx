/**
 * ContextMenu Component - Global right-click context menu
 *
 * Replaces browser's native right-click menu with custom business menu.
 * Implements smart menu items based on context:
 * - On links: Copy link, Open in new tab, Split screen open
 * - On text selection: Copy selected text
 * - On blank area: Refresh, View details, etc.
 */

import React, { useState, useEffect, useCallback, useRef, createContext, useContext } from 'react';
import { cn } from '@/utils';
import { useAppStore } from '@/store';
import { t } from '@/i18n';

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

const SplitIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <line x1="12" y1="3" x2="12" y2="21" />
  </svg>
);

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

  return (
    <ContextMenuContext.Provider value={{ state, showMenu, hideMenu }}>
      {children}
      <ContextMenuMenu />
    </ContextMenuContext.Provider>
  );
};

/**
 * ContextMenuMenu - The actual menu component rendered at click position
 */
const ContextMenuMenu: React.FC = () => {
  const { state, hideMenu } = useContextMenu();
  const menuRef = useRef<HTMLDivElement>(null);
  const language = useAppStore((state) => state.language);

  // Close on click outside
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
  }, [state.isOpen, hideMenu]);

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
      onClick: () => {
        if (state.linkUrl) {
          window.open(state.linkUrl, '_blank');
        }
        hideMenu();
      },
    },
    {
      id: 'copy-link',
      label: t('copyLink', language),
      icon: <LinkIcon />,
      visible: !!state.linkUrl,
      onClick: () => {
        if (state.linkUrl) {
          navigator.clipboard.writeText(state.linkUrl);
        }
        hideMenu();
      },
    },
    {
      id: 'split-open',
      label: t('splitScreenOpen', language),
      icon: <SplitIcon />,
      visible: !!state.linkUrl,
      onClick: () => {
        // Split screen open - could be implemented with workspace tabs
        if (state.linkUrl) {
          window.open(state.linkUrl, '_blank', 'width=800,height=600');
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
      onClick: () => {
        if (state.selectedText) {
          navigator.clipboard.writeText(state.selectedText);
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
    // General items (always visible)
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
    >
      {visibleItems.map((item) => {
        if (item.divider) {
          return <hr key={item.id} className="context-menu-divider" />;
        }

        return (
          <button
            key={item.id}
            type="button"
            className={cn(
              'context-menu-item',
              item.disabled && 'disabled',
              item.danger && 'text-danger'
            )}
            onClick={item.onClick}
            disabled={item.disabled}
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