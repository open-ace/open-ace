import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useWorkflowActivity } from './useAutonomous';

vi.mock('@/api/autonomous', () => ({
  autonomousApi: {
    getEventStreamUrl: (workflowId: string) => `/events/${workflowId}`,
  },
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.instances.push(this);
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

describe('useWorkflowActivity', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
  });

  it('keeps native EventSource reconnect enabled after a transient error', () => {
    const { unmount } = renderHook(() => useWorkflowActivity('wf-1'));
    const source = FakeEventSource.instances[0];

    act(() => source.onerror?.());
    expect(source.close).not.toHaveBeenCalled();

    unmount();
    expect(source.close).toHaveBeenCalledOnce();
  });

  it('deduplicates replayed activity and keeps the latest 50 items', () => {
    const { result } = renderHook(() => useWorkflowActivity('wf-1'));
    const source = FakeEventSource.instances[0];

    act(() => {
      for (let i = 0; i < 55; i += 1) {
        source.emit({
          event_type: 'agent_activity',
          data: {
            activity_id: `activity-${i}`,
            session_id: 'session-1',
            type: 'assistant',
            text: String(i),
            timestamp: '2026-07-21T11:00:00+00:00',
          },
        });
      }
      source.emit({
        event_type: 'agent_activity',
        data: {
          activity_id: 'activity-54',
          session_id: 'session-1',
          type: 'assistant',
          text: 'duplicate replay',
        },
      });
    });

    expect(result.current).toHaveLength(50);
    expect(result.current[0].activity_id).toBe('activity-5');
    expect(result.current.at(-1)?.text).toBe('54');
  });

  it('preserves current activity when the workflow completes and clears it on navigation', () => {
    const { result, rerender } = renderHook(
      ({ workflowId, enabled }) => useWorkflowActivity(workflowId, enabled),
      { initialProps: { workflowId: 'wf-1', enabled: true } }
    );

    act(() => {
      FakeEventSource.instances[0].emit({
        event_type: 'agent_activity',
        data: { activity_id: 'a-1', session_id: 's-1', type: 'assistant', text: 'working' },
      });
    });
    expect(result.current).toHaveLength(1);

    rerender({ workflowId: 'wf-1', enabled: false });
    expect(result.current).toHaveLength(1);

    rerender({ workflowId: 'wf-2', enabled: true });
    expect(result.current).toHaveLength(0);
  });
});
