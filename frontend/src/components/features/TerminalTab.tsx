/**
 * TerminalTab Component - Web terminal using xterm.js
 *
 * Connects to a remote machine's terminal WebSocket server
 * and provides an interactive terminal experience.
 */

import React, { useEffect, useRef, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';

interface TerminalTabProps {
  wsUrl: string;
  token: string;
  isActive: boolean;
  onError?: (error: string) => void;
}

export const TerminalTab: React.FC<TerminalTabProps> = ({
  wsUrl,
  token,
  isActive,
  onError,
}) => {
  const language = useLanguage();
  const terminalRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<any>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitAddonRef = useRef<any>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (!wsUrl || !token || !xtermRef.current) return;

    // Close existing connection
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    const wsUrlWithToken = wsUrl.includes('?')
      ? `${wsUrl}&token=${encodeURIComponent(token)}`
      : `${wsUrl}?token=${encodeURIComponent(token)}&cols=80&rows=24`;

    try {
      const ws = new WebSocket(wsUrlWithToken, ['binary']);
      ws.binaryType = 'arraybuffer';

      ws.onopen = () => {
        if (xtermRef.current) {
          xtermRef.current.writeln('\r\n\x1b[32mConnected to remote terminal.\x1b[0m\r\n');
        }
        // Send initial terminal size
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

      ws.onclose = () => {
        if (xtermRef.current) {
          xtermRef.current.writeln('\r\n\x1b[33mConnection closed. Reconnecting...\x1b[0m\r\n');
        }
        // Auto-reconnect after 3 seconds
        reconnectTimerRef.current = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        if (xtermRef.current) {
          xtermRef.current.writeln('\r\n\x1b[31mConnection error.\x1b[0m\r\n');
        }
        onError?.('Failed to connect to terminal');
      };

      wsRef.current = ws;
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      if (xtermRef.current) {
        xtermRef.current.writeln(`\r\n\x1b[31mConnection failed: ${errorMsg}\x1b[0m\r\n`);
      }
      onError?.(errorMsg);
    }
  }, [wsUrl, token, onError]);

  // Initialize xterm.js
  useEffect(() => {
    if (!terminalRef.current) return;

    let terminal: any;
    let fitAddon: any;

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

      fitAddon = new FitAddon();
      terminal.loadAddon(fitAddon);
      terminal.loadAddon(new WebLinksAddon());

      terminal.open(terminalRef.current!);
      fitAddon.fit();

      // Handle user input -> send to WebSocket
      terminal.onData((data: string) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          const encoder = new TextEncoder();
          wsRef.current.send(encoder.encode(data));
        }
      });

      // Handle resize
      terminal.onResize(({ cols, rows }: { cols: number; rows: number }) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
      });

      xtermRef.current = terminal;
      fitAddonRef.current = fitAddon;
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

  // Connect when xterm is ready
  useEffect(() => {
    if (xtermRef.current && wsUrl && token) {
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

  // Import xterm CSS
  useEffect(() => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css';
    document.head.appendChild(link);
    return () => {
      document.head.removeChild(link);
    };
  }, []);

  if (!wsUrl) {
    return (
      <div className="d-flex align-items-center justify-content-center h-100">
        <div className="text-center text-muted">
          <i className="bi bi-terminal fs-1" />
          <p className="mt-2">{t('terminalWaitingForConnection', language)}</p>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={terminalRef}
      style={{
        width: '100%',
        height: '100%',
        backgroundColor: '#1e1e2e',
        padding: '4px',
      }}
    />
  );
};
