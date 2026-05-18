/**
 * TerminalTab Component - Web terminal using xterm.js
 *
 * Connects to a remote machine's terminal WebSocket server
 * and provides an interactive terminal experience with
 * a status bar showing connection state and machine info.
 */

import React, { useEffect, useRef, useCallback, useState } from 'react';
import '@xterm/xterm/css/xterm.css';
import { useLanguage } from '@/store';
import { t } from '@/i18n';

type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'error';

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
  terminalId: _terminalId, // eslint-disable-line @typescript-eslint/no-unused-vars
  machineId: _machineId, // eslint-disable-line @typescript-eslint/no-unused-vars
  onError,
  onAuthFailed,
  onReattachNeeded,
}) => {
  const language = useLanguage();
  const terminalRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<any>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitAddonRef = useRef<any>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectCountRef = useRef(0);

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

    const wsUrlWithToken = wsUrl.includes('?')
      ? `${wsUrl}&token=${encodeURIComponent(token)}`
      : `${wsUrl}?token=${encodeURIComponent(token)}&cols=80&rows=24`;

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
                cols: dims.cols || 80,
                rows: dims.rows || 24,
              })
            );
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
        const delay = Math.min(3000 * Math.pow(1.5, reconnectCountRef.current - 1), 30000);
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
  }, [wsUrl, token]);

  // Initialize xterm.js
  useEffect(() => {
    if (!terminalRef.current) return;

    console.log('[TerminalTab] Initializing xterm.js');

    let terminal: any;

    const initTerminal = async () => {
      const { Terminal } = await import('@xterm/xterm');
      const { FitAddon } = await import('@xterm/addon-fit');
      const { WebLinksAddon } = await import('@xterm/addon-web-links');

      terminal = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        theme: {
          background: '#1e1e2e',
          foreground: '#cdd6f4',
          cursor: '#f5e0dc',
          selectionBackground: '#585b7066',
        },
        allowProposedApi: true,
      });

      const fitAddon = new FitAddon();
      terminal.loadAddon(fitAddon);
      terminal.loadAddon(new WebLinksAddon());

      terminal.open(terminalRef.current!);
      fitAddon.fit();

      terminal.onData((data: string) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          const encoder = new TextEncoder();
          wsRef.current.send(encoder.encode(data));
        }
      });

      terminal.onResize(({ cols, rows }: { cols: number; rows: number }) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
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

  // Connect when xterm is ready or wsUrl/token change
  useEffect(() => {
    console.log('[TerminalTab] Connect effect triggered:', {
      hasXterm: !!xtermRef.current,
      wsUrl,
      token: token?.substring(0, 20),
    });
    if (xtermRef.current && wsUrl && token) {
      console.log('[TerminalTab] Calling connect() from effect');
      connect();
    }
  }, [connect, wsUrl, token]);

  // Handle resize when tab becomes active
  useEffect(() => {
    if (isActive && fitAddonRef.current && terminalRef.current) {
      const timer = setTimeout(() => {
        fitAddonRef.current?.fit();
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

  return (
    <div className="d-flex flex-column h-100">
      {/* Terminal area */}
      <div
        ref={terminalRef}
        className="flex-grow-1"
        style={{
          backgroundColor: '#1e1e2e',
          padding: '4px',
          minHeight: 0,
        }}
      />

      {/* Status bar */}
      <div
        className="d-flex align-items-center px-2 py-1"
        style={{
          backgroundColor: '#181825',
          borderTop: '1px solid #313244',
          fontSize: '0.75rem',
          color: '#a6adc8',
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
