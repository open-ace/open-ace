/**
 * Tests for the RunTimeline component.
 *
 * The component pulls its data from two hooks (useRunEvents / useRunApprovals);
 * we mock them so each test controls exactly what the timeline renders. Covers:
 *  - self-hiding when the backend feature flag is off ({ disabled: true }),
 *  - loading / error states,
 *  - the run summary (attribution + cumulative usage),
 *  - the event stream (labels per event_type, tool badges, content preview),
 *  - the approvals section (status badge, request id, decided-by attribution).
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@/test/utils';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the hooks so we control what the timeline renders.
vi.mock('@/hooks', async () => {
  const actual = await vi.importActual<typeof import('@/hooks')>('@/hooks');
  return {
    ...actual,
    useRunEvents: vi.fn(),
    useRunApprovals: vi.fn(),
  };
});

import { RunTimeline } from './RunTimeline';
import { useRunEvents, useRunApprovals } from '@/hooks';
import type {
  AgentRun,
  RunEvent,
  AgentApproval,
  RunEventsResponse,
  RunApprovalsResponse,
} from '@/api';

const mockedUseRunEvents = vi.mocked(useRunEvents);
const mockedUseRunApprovals = vi.mocked(useRunApprovals);

// ── Builders ───────────────────────────────────────────────────────────────

const mkRun = (over: Partial<AgentRun> = {}): AgentRun => ({
  run_id: 'run-1',
  session_id: 'sess-1',
  user_id: 7,
  tenant_id: 1,
  machine_id: 'mac-1',
  tool_name: 'claude-code',
  provider: 'anthropic',
  cli_tool: 'claude-code',
  model: 'sonnet',
  status: 'active',
  started_at: '2026-06-24T01:02:03Z',
  ended_at: null,
  total_tokens: 0,
  total_input_tokens: 0,
  total_output_tokens: 0,
  total_requests: 0,
  metadata: {},
  created_at: null,
  updated_at: null,
  ...over,
});

const mkEvent = (over: Partial<RunEvent> = {}): RunEvent => ({
  id: 1,
  run_id: 'run-1',
  session_id: 'sess-1',
  event_type: 'session_created',
  event_subtype: null,
  role: null,
  content: null,
  tool_name: null,
  provider: null,
  model: null,
  key_id: null,
  user_id: 7,
  tenant_id: 1,
  machine_id: 'mac-1',
  metadata: {},
  event_ts: '2026-06-24T01:02:04Z',
  created_at: null,
  ...over,
});

const mkApproval = (over: Partial<AgentApproval> = {}): AgentApproval => ({
  id: 1,
  request_id: 'req-1',
  run_id: 'run-1',
  session_id: 'sess-1',
  tool_name: 'Bash',
  request_subtype: 'execute',
  request_details: {},
  status: 'pending',
  decision: null,
  decided_by: null,
  decided_by_name: null,
  decision_metadata: {},
  requested_at: '2026-06-24T01:02:05Z',
  decided_at: null,
  created_at: null,
  updated_at: null,
  ...over,
});

const eventsResponse = (run: AgentRun | null, events: RunEvent[]): RunEventsResponse => ({
  success: true,
  run,
  events,
  total: events.length,
  limit: 50,
  offset: 0,
});

const approvalsResponse = (approvals: AgentApproval[]): RunApprovalsResponse => ({
  success: true,
  approvals,
  total: approvals.length,
});

const renderWithProviders = (ui: ReactNode) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

// ── Tests ──────────────────────────────────────────────────────────────────

describe('RunTimeline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: no approvals, idle approvals query.
    mockedUseRunApprovals.mockReturnValue({
      data: approvalsResponse([]),
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useRunApprovals>);
  });

  describe('feature flag / disabled state', () => {
    it('renders nothing when the backend reports disabled', () => {
      mockedUseRunEvents.mockReturnValue({
        data: {
          success: true,
          disabled: true,
          run: null,
          events: [],
          total: 0,
          limit: 50,
          offset: 0,
        },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useRunEvents>);

      const { container } = renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      expect(container).toBeEmptyDOMElement();
    });

    it('does not query approvals while disabled', () => {
      mockedUseRunEvents.mockReturnValue({
        data: {
          success: true,
          disabled: true,
          run: null,
          events: [],
          total: 0,
          limit: 50,
          offset: 0,
        },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useRunEvents>);

      renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      // When disabled the component passes null as the query key.
      expect(mockedUseRunApprovals).toHaveBeenCalledWith(null);
    });
  });

  describe('loading and error', () => {
    it('renders the loading state', () => {
      mockedUseRunEvents.mockReturnValue({
        data: undefined,
        isLoading: true,
        isError: false,
      } as unknown as ReturnType<typeof useRunEvents>);

      renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      expect(screen.getAllByText('Loading timeline...').length).toBeGreaterThan(0);
    });

    it('renders the error state with the failure message', () => {
      mockedUseRunEvents.mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: { message: 'boom' },
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof useRunEvents>);

      renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      expect(screen.getByText(/Failed to load timeline: boom/)).toBeInTheDocument();
    });
  });

  describe('run summary + event stream', () => {
    beforeEach(() => {
      mockedUseRunEvents.mockReturnValue({
        data: eventsResponse(mkRun({ total_requests: 3 }), [
          mkEvent({ id: 1, event_type: 'session_created' }),
          mkEvent({ id: 2, event_type: 'tool_use', tool_name: 'Bash', content: 'ls -la' }),
        ]),
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useRunEvents>);
    });

    it('renders the run status badge and attribution summary', () => {
      renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      // Status badge.
      expect(screen.getByText('active')).toBeInTheDocument();
      // Attribution fields.
      expect(screen.getByText('sonnet')).toBeInTheDocument(); // model
      expect(screen.getByText('anthropic')).toBeInTheDocument(); // provider
      // tool_name + cli_tool both render 'claude-code' → ≥1 occurrence.
      expect(screen.getAllByText('claude-code').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('mac-1')).toBeInTheDocument(); // machine
      expect(screen.getByText('3')).toBeInTheDocument(); // total requests
    });

    it('renders an event row per event with its label and tool badge', () => {
      renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      expect(screen.getByText('Session created')).toBeInTheDocument();
      expect(screen.getByText('Tool use')).toBeInTheDocument();
      // The Bash tool badge appears on the tool_use event.
      expect(screen.getAllByText('Bash').length).toBeGreaterThanOrEqual(1);
      // Tool-use content preview.
      expect(screen.getByText('ls -la')).toBeInTheDocument();
    });

    it('shows an empty state when there are no events', () => {
      mockedUseRunEvents.mockReturnValue({
        data: eventsResponse(mkRun(), []),
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useRunEvents>);

      renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      expect(screen.getByText('No events recorded yet')).toBeInTheDocument();
    });
  });

  describe('approvals', () => {
    beforeEach(() => {
      mockedUseRunEvents.mockReturnValue({
        data: eventsResponse(mkRun(), [mkEvent()]),
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useRunEvents>);
    });

    it('hides the approvals section when there are none', () => {
      mockedUseRunApprovals.mockReturnValue({
        data: approvalsResponse([]),
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useRunApprovals>);

      renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      expect(screen.queryByText('Approvals')).not.toBeInTheDocument();
    });

    it('renders pending and approved approvals with request ids and deciders', () => {
      mockedUseRunApprovals.mockReturnValue({
        data: approvalsResponse([
          mkApproval({ request_id: 'req-1', status: 'pending' }),
          mkApproval({
            request_id: 'req-2',
            status: 'approved',
            decision: 'allow',
            decided_by_name: 'alice',
            decided_at: '2026-06-24T01:03:00Z',
          }),
        ]),
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useRunApprovals>);

      renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      // Status badges.
      expect(screen.getByText('Pending')).toBeInTheDocument();
      expect(screen.getByText('Approved')).toBeInTheDocument();
      // Request ids rendered as <code>.
      expect(screen.getByText('req-1')).toBeInTheDocument();
      expect(screen.getByText('req-2')).toBeInTheDocument();
      // Approved row names its decider.
      expect(screen.getByText(/alice/)).toBeInTheDocument();
    });
  });

  describe('expand / collapse', () => {
    it('truncates long content and expands on click', () => {
      const longContent = 'x'.repeat(200);
      mockedUseRunEvents.mockReturnValue({
        data: eventsResponse(mkRun(), [
          mkEvent({ id: 1, event_type: 'assistant_output', content: longContent }),
        ]),
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useRunEvents>);

      renderWithProviders(<RunTimeline sessionId="sess-1" language="en" />);
      // Collapsed: a "Show more" toggle exists and content is truncated.
      const toggle = screen.getByRole('button', { name: /Show more/i });
      expect(toggle).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Show less/i })).not.toBeInTheDocument();

      // Expand → full content is shown and the toggle flips.
      fireEvent.click(toggle);
      expect(screen.getByRole('button', { name: /Show less/i })).toBeInTheDocument();
    });
  });
});
