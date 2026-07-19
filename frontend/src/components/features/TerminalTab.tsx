/**
 * TerminalTab Component - Web terminal using xterm.js
 *
 * Connects to a remote machine's terminal WebSocket server
 * and provides an interactive terminal experience with
 * a status bar showing connection state and machine info.
 *
 * HA Support (Issue #1851):
 * - Handles WebSocket redirect (close code 3010)
 * - Exponential backoff reconnection
 * - Graceful reconnection after relay disconnect
 */

import React, { useEffect, useRef, useCallback, useState } from 'react';
import '@xterm/xterm/css/xterm.css';
import type { Terminal } from '@xterm/xterm';
import type { FitAddon } from '@xterm/addon-fit';
import { useLanguage, useTheme } from '@/store';
import { t } from '@/i18n';

type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'error';

// WebSocket close codes for HA redirect (Issue #1851)
const REDIRECT_CLOSE_CODE = 3010;
const RELAY_DISCONNECTED_CODE = 1012;

interface TerminalTabProps {
  wsUrl: string;
  token: string;
  isActive: boolean;
  machineName?: string;
  terminalId?: string;
  machineId?: string;
  onError?: (error: string) => void;
  onAuthFailed?: () => void;
  onReattachNeeded?: () => void;
}

export const TerminalTab: React.FC<TerminalTabProps> = ({
  wsUrl,
  token,
  isActive,
  machineName,
  terminalId: _terminalId,
  machineId: _machineId,
  onError,
  onAuthFailed,
  onReattachNeeded,
}) => {
  const language = useLanguage();
  const theme = useTheme();
  const terminalRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectCountRef = useRef(0);
  const isActiveRef = useRef(isActive);
  isActiveRef.current = isActive;

  // Helper function to read CSS variable values - Issue #1334
  const getCSSVariable = (varName: string): string => {
    const value = window
      .getComputedStyle(document.documentElement)
      .getPropertyValue(varName)
      .trim();
    return value || '';
  };

  // Use ref for theme to avoid stale closure in async initTerminal (Issue #637)
  const themeRef = useRef(theme);
  themeRef.current = theme;

  // Use refs for callbacks to avoid stale closures and unnecessary reconnects
  const onReattachNeededRef = useRef(onReattachNeeded);
  onReattachNeededRef.current = onReattachNeeded;
  const onAuthFailedRef = useRef(onAuthFailed);
  onAuthFailedRef.current = onAuthFailed;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');

  const connect = useCallback(() => {
    console.log('[TerminalTab] connect called:', {
      wsUrl,
      hasToken: !!token,
      hasXterm: !!xtermRef.current,
    });
    if (!wsUrl || !token || !xtermRef.current) {
      console.log('[TerminalTab] Skipping connect - missing requirements');
      return;
    }

    // Skip if already connecting or connected
    if (
      wsRef.current &&
      (wsRef.current.readyState === WebSocket.CONNECTING ||
        wsRef.current.readyState === WebSocket.OPEN)
    ) {
      console.log('[TerminalTab] Skipping connect - already connected/connecting');
      return;
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setConnectionState('connecting');

    try {
      if (!wsUrl.trim()) {
        setConnectionState('disconnected');
        return;
      }
      const absoluteWsUrl = wsUrl.startsWith('/')
        ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}${wsUrl}`
        : wsUrl;
      const terminalUrl = new URL(absoluteWsUrl);
      terminalUrl.searchParams.set('token', token);
      if (!terminalUrl.searchParams.has('cols')) terminalUrl.searchParams.set('cols', '80');
      if (!terminalUrl.searchParams.has('rows')) terminalUrl.searchParams.set('rows', '24');
      const wsUrlWithToken = terminalUrl.toString();

      createWebSocket(wsUrlWithToken);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setConnectionState('error');
      if (xtermRef.current) {
        xtermRef.current.writeln(`\r\n\x1b[31mConnection failed: ${errorMsg}\x1b[0m\r\n`);
      }
      onErrorRef.current?.(errorMsg);
    }
  }, [wsUrl, token]);

  // HA: Connect with a specific URL (for redirect support)
  const connectWithUrl = useCallback((targetUrl: string) => {
    console.log('[TerminalTab] connectWithUrl called:', targetUrl.substring(0, 50) + '...');
    if (!xtermRef.current) {
      console.log('[TerminalTab] Skipping connectWithUrl - no xterm');
      return;
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setConnectionState('connecting');

    // Ensure token is in the URL
    try {
      const url = new URL(targetUrl);
      if (!url.searchParams.has('token') && token) {
        url.searchParams.set('token', token);
      }
      if (!url.searchParams.has('cols')) url.searchParams.set('cols', '80');
      if (!url.searchParams.has('rows')) url.searchParams.set('rows', '24');
      createWebSocket(url.toString());
    } catch (err) {
      console.error('[TerminalTab] Invalid redirect URL:', err);
      // Fallback to original URL
      connect();
    }
  }, [token, connect]);

  // Create WebSocket with the given URL
  const createWebSocket = useCallback((wsUrlWithToken: string) => {
    try {
      const ws = new WebSocket(wsUrlWithToken, ['binary']);
      console.log('[TerminalTab] WebSocket created, URL length:', wsUrlWithToken.length);
      ws.binaryType = 'arraybuffer';

      ws.onopen = () => {
        console.log('[TerminalTab] WebSocket OPENED');
        setConnectionState('connected');
        reconnectCountRef.current = 0;
        if (xtermRef.current) {
          xtermRef.current.writeln('\r\n\x1b[32mConnected to remote terminal.\x1b[0m\r\n');
        }
        if (fitAddonRef.current && xtermRef.current) {
          const dims = fitAddonRef.current.proposeDimensions();
          if (dims) {
            ws.send(
              JSON.stringify({
                type: 'resize',
                cols: dims.cols ?? 80,
                rows: dims.rows ?? 24,
              })
            );
          }
        }
        if (isActiveRef.current) {
          try {
            xtermRef.current?.focus();
          } catch {
            // Ignore focus errors
          }
        }
      };

      ws.onmessage = (event) => {
        if (!xtermRef.current) return;
        if (event.data instanceof ArrayBuffer) {
          const text = new TextDecoder().decode(event.data);
          xtermRef.current.write(text);
        } else if (typeof event.data === 'string') {
          xtermRef.current.write(event.data);
        }
      };

      ws.onclose = (event) => {
        console.log('[TerminalTab] WebSocket CLOSED:', event.code, event.reason);
        setConnectionState('disconnected');

        // HA: Handle redirect from another Pod (Issue #1851)
        if (event.code === REDIRECT_CLOSE_CODE && event.reason) {
          const redirectUrl = event.reason;
          if (xtermRef.current) {
            xtermRef.current.writeln(
              '\r\n\x1b[36mRedirecting to owner pod...\x1b[0m\r\n'
            );
          }
          console.log('[TerminalTab] Redirect to:', redirectUrl.substring(0, 50) + '...');
          // Reconnect to redirect URL after short delay
          setTimeout(() => {
            if (isActiveRef.current) {
              connectWithUrl(redirectUrl);
            }
          }, 1000);
          return;
        }

        // HA: Handle relay disconnected (Issue #1851)
        if (event.code === RELAY_DISCONNECTED_CODE) {
          if (xtermRef.current) {
            xtermRef.current.writeln(
              '\r\n\x1b[33mRelay disconnected. Reconnecting...\x1b[0m\r\n'
            );
          }
          reconnectCountRef.current += 1;
          // Exponential backoff: 1s → 2s → 4s → 8s (max 30s)
          const delay = Math.min(1000 * Math.pow(2, reconnectCountRef.current - 1), 30000);
          reconnectTimerRef.current = setTimeout(connect, delay);
          return;
        }

        if (event.code === 4001) {
          if (xtermRef.current) {
            xtermRef.current.writeln(
              '\r\n\x1b[33mAuthentication failed. Reconnecting...\x1b[0m\r\n'
            );
          }
          onAuthFailedRef.current?.();
          return;
        }
        if (xtermRef.current) {
          xtermRef.current.writeln('\r\n\x1b[33mConnection closed. Reconnecting...\x1b[0m\r\n');
        }
        reconnectCountRef.current += 1;
        // After 5 failed reconnects, trigger reattach
        if (reconnectCountRef.current >= 5 && onReattachNeededRef.current) {
          console.log('[TerminalTab] Too many reconnect failures, triggering reattach');
          if (xtermRef.current) {
            xtermRef.current.writeln(
              '\r\n\x1b[36mRequesting new terminal connection...\x1b[0m\r\n'
            );
          }
          onReattachNeededRef.current();
          return;
        }
        // Exponential backoff: 1s → 2s → 4s → 8s (max 30s)
        const delay = Math.min(1000 * Math.pow(2, reconnectCountRef.current - 1), 30000);
        reconnectTimerRef.current = setTimeout(connect, delay);
      };

      ws.onerror = (error) => {
        console.log('[TerminalTab] WebSocket ERROR:', error);
        setConnectionState('error');
        if (xtermRef.current) {
          xtermRef.current.writeln('\r\n\x1b[31mConnection error.\x1b[0m\r\n');
        }
        onErrorRef.current?.('Failed to connect to terminal');
      };

      wsRef.current = ws;
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setConnectionState('error');
      if (xtermRef.current) {
        xtermRef.current.writeln(`\r\n\x1b[31mConnection failed: ${errorMsg}\x1b[0m\r\n`);
      }
      onErrorRef.current?.(errorMsg);
    }
  }, [connect, connectWithUrl]);

  // Initialize xterm.js
  useEffect(() => {
    if (!terminalRef.current) return;

    console.log('[TerminalTab] Initializing xterm.js');

    let terminal: Terminal | null = null;

    const initTerminal = async () => {
      const { Terminal } = await import('@xterm/xterm');
      const { FitAddon } = await import('@xterm/addon-fit');
      const { WebLinksAddon } = await import('@xterm/addon-web-links');

      // Use current theme for initial setup via ref (Issue #637)
      const currentTheme = themeRef.current;
      // Use CSS variables for terminal theme - Issue #1334
      const initialTheme = {
        background:
          getCSSVariable('--terminal-bg') || (currentTheme === 'dark' ? '#1e1e2e' : '#ffffff'),
        foreground:
          getCSSVariable('--terminal-fg') || (currentTheme === 'dark' ? '#cdd6f4' : '#1e1e2e'),
        cursor:
          getCSSVariable('--terminal-cursor') || (currentTheme === 'dark' ? '#f5e0dc' : '#1e1e2e'),
        selectionBackground:
          getCSSVariable('--terminal-selection') ||
          (currentTheme === 'dark' ? '#585b7066' : '#add8e666'),
      };

      terminal = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        theme: initialTheme,
        allowProposedApi: true,
      });

      const fitAddon = new FitAddon();
      terminal.loadAddon(fitAddon);
      terminal.loadAddon(new WebLinksAddon());

      terminal.open(terminalRef.current ?? document.body);
      fitAddon.fit();

      terminal.onData((data: string) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          const encoder = new TextEncoder();
          wsRef.current.send(encoder.encode(data));
        }
      });

      terminal.onResize(({ cols, rows }: { cols: number; rows: number }) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
      });

      xtermRef.current = terminal;
      fitAddonRef.current = fitAddon;

      console.log('[TerminalTab] xterm.js initialized, ready to connect');

      // Don't auto-connect here - let the connect effect handle it
    };

    initTerminal();

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (terminal) {
        terminal.dispose();
      }
    };
  }, []);

  // Dynamic theme update without restarting terminal (Issue #637, #1334)
  useEffect(() => {
    if (!xtermRef.current) return;

    // Use CSS variables for terminal theme - Issue #1334
    const terminalTheme = {
      background: getCSSVariable('--terminal-bg') || (theme === 'dark' ? '#1e1e2e' : '#ffffff'),
      foreground: getCSSVariable('--terminal-fg') || (theme === 'dark' ? '#cdd6f4' : '#1e1e2e'),
      cursor: getCSSVariable('--terminal-cursor') || (theme === 'dark' ? '#f5e0dc' : '#1e1e2e'),
      selectionBackground:
        getCSSVariable('--terminal-selection') || (theme === 'dark' ? '#585b7066' : '#add8e666'),
    };

    xtermRef.current.options.theme = terminalTheme;
  }, [theme]);

  // Connect when xterm is ready or wsUrl/token change
  useEffect(() => {
    console.log('[TerminalTab] Connect effect triggered:', {
      hasXterm: !!xtermRef.current,
      wsUrl,
      token: !!token,
    });
    if (xtermRef.current && wsUrl && token) {
      console.log('[TerminalTab] Calling connect() from effect');
      connect();
    }
  }, [connect, wsUrl, token]);

  // Handle resize and focus when tab becomes active
  useEffect(() => {
    if (isActive && fitAddonRef.current && terminalRef.current) {
      const timer = setTimeout(() => {
        fitAddonRef.current?.fit();
        try {
          xtermRef.current?.focus();
        } catch {
          // Ignore focus errors
        }
      }, 100);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [isActive]);

  // Handle window resize
  useEffect(() => {
    const handleResize = () => {
      if (fitAddonRef.current) {
        fitAddonRef.current.fit();
      }
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Connection status indicator
  const statusColor: Record<ConnectionState, string> = {
    connecting: '#f59e0b',
    connected: '#22c55e',
    disconnected: '#6b7280',
    error: '#ef4444',
  };
  const statusText: Record<ConnectionState, string> = {
    connecting: t('terminalConnecting', language) || 'Connecting...',
    connected: t('terminalConnected', language) || 'Connected',
    disconnected: t('terminalDisconnected', language) || 'Disconnected',
    error: t('terminalError', language) || 'Error',
  };

  // Always render terminal div to allow xterm.js initialization
  // The terminal will show "waiting for connection" state if wsUrl is empty

  // Dynamic styles based on theme using CSS variables (Issue #637, #1334)
  const terminalBgColor =
    getCSSVariable('--terminal-bg') || (theme === 'dark' ? '#1e1e2e' : '#ffffff');
  const statusBarBgColor =
    getCSSVariable('--terminal-status-bar-bg') || (theme === 'dark' ? '#181825' : '#f8fafc');
  const statusBarBorderColor =
    getCSSVariable('--terminal-status-bar-border') || (theme === 'dark' ? '#313244' : '#e2e8f0');
  const statusBarTextColor =
    getCSSVariable('--terminal-status-bar-text') || (theme === 'dark' ? '#a6adc8' : '#475569');

  return (
    <div className="d-flex flex-column h-100">
      {/* Terminal area */}
      <div
        ref={terminalRef}
        className="flex-grow-1"
        style={{
          backgroundColor: terminalBgColor,
          padding: '4px',
          minHeight: 0,
        }}
      />

      {/* Status bar */}
      <div
        className="d-flex align-items-center px-2 py-1"
        style={{
          backgroundColor: statusBarBgColor,
          borderTop: `1px solid ${statusBarBorderColor}`,
          fontSize: '0.75rem',
          color: statusBarTextColor,
          flexShrink: 0,
        }}
      >
        {/* Connection status dot */}
        <span
          style={{
            display: 'inline-block',
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            backgroundColor: statusColor[connectionState],
            marginRight: '6px',
            animation:
              connectionState === 'connecting' ? 'pulse 1.5s ease-in-out infinite' : 'none',
          }}
        />
        <span className="me-3">{statusText[connectionState]}</span>

        {/* Machine name */}
        {machineName && (
          <span className="me-3">
            <i className="bi bi-cloud-fill text-primary me-1" style={{ fontSize: '0.65rem' }} />
            {machineName}
          </span>
        )}

        {/* Spacer */}
        <span className="flex-grow-1" />

        {/* Right side info */}
        <span className="text-muted" style={{ fontSize: '0.7rem' }}>
          <i className="bi bi-terminal me-1" />
          Claude Code
        </span>
      </div>

      {/* Pulse animation */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
};
