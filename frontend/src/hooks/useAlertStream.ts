/**
 * useAlertStream Hook - SSE connection management for real-time alerts
 *
 * Issue #3332: Implements EventSource connection with native reconnection
 * and localStorage-based deduplication.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAlertStreamStore } from '@/store/alertStreamStore';
import type { Alert } from '@/api';
import { alertsApi } from '@/api';

interface SSEMessage {
  type: 'connected' | 'alert' | 'error' | 'unread_count' | 'alert_read';
  user_id?: number;
  data?: Alert;
  count?: number;
  alert_id?: string;
  message?: string;
}

interface UseAlertStreamOptions {
  url?: string;
  enabled?: boolean;
  onAlert?: (alert: Alert) => void;
  onError?: (error: string) => void;
}

interface UseAlertStreamReturn {
  connectionStatus: 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'error';
  unreadCount: number;
  alerts: Alert[];
  reconnect: () => void;
}

// Global EventSource instance (singleton pattern)
let globalEventSource: EventSource | null = null;
let connectionCount = 0;

export const useAlertStream = (
  options: UseAlertStreamOptions = {}
): UseAlertStreamReturn => {
  const {
    url = '/api/alerts/stream',
    enabled = true,
    onAlert,
    onError,
  } = options;

  const queryClient = useQueryClient();
  const {
    setConnectionStatus,
    addAlert,
    setAlerts,
    markAlertAsRead,
    incrementUnreadCount,
    decrementUnreadCount,
  } = useAlertStreamStore();

  const connectionStatus = useAlertStreamStore((state) => state.connectionStatus);
  const unreadCount = useAlertStreamStore((state) => state.unreadCount);
  const alerts = useAlertStreamStore((state) => state.alerts);

  const [fallbackToPolling, setFallbackToPolling] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttemptsRef = useRef(0);

  // Fetch alerts function for reconnection and fallback
  const fetchAlerts = useCallback(async () => {
    try {
      const result = await alertsApi.getAlerts({ limit: 100 });
      setAlerts(result.alerts);
      // Note: unreadCount is managed separately by the store
    } catch (error) {
      console.error('Failed to fetch alerts:', error);
    }
  }, [setAlerts]);

  // Handle incoming SSE messages
  const handleMessage = useCallback(
    (event: MessageEvent) => {
      try {
        const message: SSEMessage = JSON.parse(event.data);

        switch (message.type) {
          case 'connected':
            console.log('SSE connected:', message.user_id);
            setConnectionStatus('connected');
            reconnectAttemptsRef.current = 0;
            // Fetch latest alerts on reconnection to catch missed alerts
            fetchAlerts();
            break;

          case 'alert':
            if (message.data) {
              addAlert(message.data);
              // Invalidate React Query cache
              queryClient.invalidateQueries({ queryKey: ['alerts'] });
              queryClient.invalidateQueries({ queryKey: ['quota-usage'] });
              // Call optional callback
              onAlert?.(message.data);
            }
            break;

          case 'alert_read':
            if (message.alert_id) {
              markAlertAsRead(message.alert_id);
            }
            break;

          case 'unread_count':
            if (typeof message.count === 'number') {
              // Update unread count from server
              useAlertStreamStore.setState({ unreadCount: message.count });
            }
            break;

          case 'error':
            console.error('SSE error:', message.message);
            onError?.(message.message || 'Unknown error');
            break;
        }
      } catch (error) {
        console.error('Failed to parse SSE message:', error);
      }
    },
    [
      addAlert,
      fetchAlerts,
      markAlertAsRead,
      onError,
      onAlert,
      queryClient,
      setConnectionStatus,
    ]
  );

  // Connect to SSE endpoint
  const connect = useCallback(() => {
    // Check browser support
    if (typeof window === 'undefined' || !window.EventSource) {
      console.warn('EventSource not supported, falling back to polling');
      setFallbackToPolling(true);
      return;
    }

    // Don't create multiple connections
    if (globalEventSource && globalEventSource.readyState !== EventSource.CLOSED) {
      return;
    }

    setConnectionStatus('connecting');

    try {
      globalEventSource = new EventSource(url);

      globalEventSource.onopen = () => {
        console.log('EventSource opened');
        setConnectionStatus('connected');
        reconnectAttemptsRef.current = 0;
      };

      globalEventSource.onmessage = handleMessage;

      globalEventSource.onerror = (e) => {
        if (globalEventSource?.readyState === EventSource.CLOSED) {
          // Native reconnection failed, try manual reconnect
          console.log('EventSource closed, attempting manual reconnect');
          setConnectionStatus('error');
          const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 30000);
          setTimeout(() => {
            if (reconnectAttemptsRef.current < 10) {
              reconnectAttemptsRef.current++;
              connect();
            } else {
              // Max retries reached, fall back to polling
              console.warn('Max SSE reconnect attempts reached, falling back to polling');
              setFallbackToPolling(true);
            }
          }, delay);
        } else {
          // Connection is reconnecting (native behavior)
          setConnectionStatus('reconnecting');
        }
      };
    } catch (error) {
      console.error('Failed to create EventSource:', error);
      setFallbackToPolling(true);
    }
  }, [handleMessage, setConnectionStatus, url]);

  // Manual reconnect
  const reconnect = useCallback(() => {
    // Close existing connection
    if (globalEventSource) {
      globalEventSource.close();
      globalEventSource = null;
    }
    setFallbackToPolling(false);
    reconnectAttemptsRef.current = 0;
    connect();
  }, [connect]);

  // Effect: Connect on mount, disconnect on unmount
  useEffect(() => {
    if (!enabled) {
      return;
    }

    connectionCount++;

    // Only connect if this is the first component
    if (connectionCount === 1) {
      connect();
    }

    return () => {
      connectionCount--;

      // Only disconnect if this is the last component
      if (connectionCount === 0) {
        if (globalEventSource) {
          globalEventSource.close();
          globalEventSource = null;
        }
        setConnectionStatus('disconnected');
      }

      // Clear polling timer
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [connect, enabled, setConnectionStatus]);

  // Effect: Fallback to polling
  useEffect(() => {
    if (fallbackToPolling && enabled) {
      console.log('Starting polling fallback (30s interval)');
      fetchAlerts(); // Initial fetch
      pollingRef.current = setInterval(fetchAlerts, 30000);

      return () => {
        if (pollingRef.current) {
          clearInterval(pollingRef.current);
          pollingRef.current = null;
        }
      };
    }
  }, [fallbackToPolling, enabled, fetchAlerts]);

  return {
    connectionStatus,
    unreadCount,
    alerts,
    reconnect,
  };
};

export default useAlertStream;