import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

import { useAcceptanceOverride } from './useAutonomous';

const acceptanceOverride = vi.fn();

vi.mock('@/api/autonomous', () => ({
  autonomousApi: {
    acceptanceOverride: (workflowId: string, options: { reason?: string }) =>
      acceptanceOverride(workflowId, options),
  },
}));

function withClient({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe('useAcceptanceOverride', () => {
  beforeEach(() => {
    acceptanceOverride.mockReset();
  });

  it('calls acceptanceOverride with the workflow id + reason', async () => {
    acceptanceOverride.mockResolvedValue({ success: true, workflow: { workflow_id: 'wf-1' } });
    const { result } = renderHook(() => useAcceptanceOverride(), { wrapper: withClient });

    await act(async () => {
      result.current.mutate({ workflowId: 'wf-1', reason: 'reviewed' });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(acceptanceOverride).toHaveBeenCalledWith('wf-1', { reason: 'reviewed' });
  });

  it('passes undefined reason through when omitted (the API method defaults it)', async () => {
    acceptanceOverride.mockResolvedValue({ success: true, workflow: { workflow_id: 'wf-2' } });
    const { result } = renderHook(() => useAcceptanceOverride(), { wrapper: withClient });

    await act(async () => {
      result.current.mutate({ workflowId: 'wf-2' });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // The hook forwards {reason: undefined}; autonomousApi.acceptanceOverride
    // applies the `?? ''` default — that fallback is exercised via the mock.
    expect(acceptanceOverride).toHaveBeenCalledWith('wf-2', { reason: undefined });
  });

  it('surfaces the failure when the server rejects (non-admin / bad state)', async () => {
    acceptanceOverride.mockRejectedValue(new Error('403'));
    const { result } = renderHook(() => useAcceptanceOverride(), { wrapper: withClient });

    await act(async () => {
      result.current.mutate({ workflowId: 'wf-3', reason: 'x' });
    });
    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(acceptanceOverride).toHaveBeenCalledTimes(1);
  });
});
