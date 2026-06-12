/**
 * useGlobalRefreshPause Hook - Global pause control for all page refreshes
 *
 * Features:
 * - Pause/resume all auto refreshes
 * - Keyboard shortcut support
 * - Status indication
 */

import { useEffect, useCallback } from 'react';
import { usePageRefreshStore } from '@/store';

/**
 * Keyboard shortcut key (default: Ctrl+Shift+P)
 */
const SHORTCUT_KEY = 'P';
const SHORTCUT_MODIFIERS = {
  ctrl: true,
  shift: true,
};

/**
 * useGlobalRefreshPause Hook
 */
export function useGlobalRefreshPause() {
  const globalPaused = usePageRefreshStore((state) => state.globalPaused);
  const pauseAll = usePageRefreshStore((state) => state.pauseAll);
  const resumeAll = usePageRefreshStore((state) => state.resumeAll);

  /**
   * Toggle global pause
   */
  const togglePause = useCallback(() => {
    if (globalPaused) {
      resumeAll();
    } else {
      pauseAll();
    }
  }, [globalPaused, pauseAll, resumeAll]);

  /**
   * Keyboard shortcut handler
   */
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // Check if shortcut key is pressed
      if (
        event.key === SHORTCUT_KEY &&
        event.ctrlKey === SHORTCUT_MODIFIERS.ctrl &&
        event.shiftKey === SHORTCUT_MODIFIERS.shift
      ) {
        event.preventDefault();
        togglePause();
      }
    };

    window.addEventListener('keydown', handleKeyDown);

    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [togglePause]);

  return {
    globalPaused,
    pauseAll,
    resumeAll,
    togglePause,
  };
}