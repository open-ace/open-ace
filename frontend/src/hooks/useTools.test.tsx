/**
 * Tests for dynamic tool options pattern used across components
 *
 * Verifies that the useTools hook + formatToolName pattern correctly
 * builds tool dropdown options from dynamic API data.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useTools } from '@/hooks/useMessages';
import { formatToolName } from '@/utils';

// Mock the messagesApi module
vi.mock('@/api', () => ({
  messagesApi: {
    getTools: vi.fn(),
  },
}));

import { messagesApi } from '@/api';

const mockedGetTools = vi.mocked(messagesApi.getTools);

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
        staleTime: 0,
      },
    },
  });

const createWrapper = () => {
  const queryClient = createQueryClient();
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

describe('useTools hook', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should return tools from API', async () => {
    const mockTools = ['openclaw', 'claude', 'qwen', 'codex'];
    mockedGetTools.mockResolvedValueOnce(mockTools);

    const { result } = renderHook(() => useTools(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toEqual(mockTools);
  });

  it('should return empty array when API returns empty', async () => {
    mockedGetTools.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useTools(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toEqual([]);
  });
});

describe('dynamic toolOptions construction', () => {
  it('should build correct options from tools array', () => {
    const tools = ['openclaw', 'claude', 'qwen', 'codex'];

    // Simulate the useMemo logic used in components
    const toolOptions = [
      { value: '', label: 'All Tools' },
      ...tools.map((tool) => ({ value: tool, label: formatToolName(tool) })),
    ];

    expect(toolOptions).toEqual([
      { value: '', label: 'All Tools' },
      { value: 'openclaw', label: 'OpenClaw' },
      { value: 'claude', label: 'Claude' },
      { value: 'qwen', label: 'Qwen' },
      { value: 'codex', label: 'Codex' },
    ]);
  });

  it('should handle tools not in TOOL_DISPLAY_NAMES gracefully', () => {
    const tools = ['new_tool', 'another_tool', 'claude'];

    const toolOptions = [
      { value: '', label: 'All Tools' },
      ...tools.map((tool) => ({ value: tool, label: formatToolName(tool) })),
    ];

    expect(toolOptions).toEqual([
      { value: '', label: 'All Tools' },
      { value: 'new_tool', label: 'New_tool' },
      { value: 'another_tool', label: 'Another_tool' },
      { value: 'claude', label: 'Claude' },
    ]);
  });

  it('should produce only the "All" option when tools array is empty', () => {
    const tools: string[] = [];

    const toolOptions = [
      { value: '', label: 'All Tools' },
      ...tools.map((tool) => ({ value: tool, label: formatToolName(tool) })),
    ];

    expect(toolOptions).toEqual([{ value: '', label: 'All Tools' }]);
  });

  it('should handle null/undefined toolsData with fallback', () => {
    // Simulate: const tools = useMemo(() => toolsData ?? [], [toolsData])
    const toolsData: string[] | null | undefined = null;
    const tools = toolsData ?? [];

    const toolOptions = [
      { value: '', label: 'All Tools' },
      ...tools.map((tool) => ({ value: tool, label: formatToolName(tool) })),
    ];

    expect(toolOptions).toEqual([{ value: '', label: 'All Tools' }]);
  });

  it('should dynamically include new tools', () => {
    // This is the key test: tools are now dynamic, not hardcoded
    const apiTools = ['openclaw', 'claude', 'qwen', 'codex', 'future_tool'];

    const toolOptions = [
      { value: '', label: 'All Tools' },
      ...apiTools.map((tool) => ({ value: tool, label: formatToolName(tool) })),
    ];

    expect(toolOptions).toHaveLength(6);
    expect(toolOptions.map((o) => o.value)).toEqual([
      '',
      'openclaw',
      'claude',
      'qwen',
      'codex',
      'future_tool',
    ]);
    // Unknown tool falls back to capitalized first letter
    expect(toolOptions[5].label).toBe('Future_tool');
  });
});
