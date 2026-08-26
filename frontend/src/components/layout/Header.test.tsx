/**
 * Header Component Tests - Notification Bell & Unread Alert Badge
 *
 * Tests cover:
 * - Notification bell renders when authenticated
 * - Notification bell hidden when not authenticated
 * - Clicking bell navigates to /manage/quota
 * - Polling fetches unread count on mount and every 30s
 * - Polling cleans up on unmount
 * - API error handled gracefully
 */

import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

// Mutable mock state
let mockIsAuthenticated = true;
const mockGetUnreadCount = vi.fn();
const mockNavigate = vi.fn();

// Mock hooks
vi.mock('@/hooks', () => ({
  useAuth: () => ({
    user: { username: 'testuser', email: 'test@test.com', avatar_url: null },
    isAuthenticated: mockIsAuthenticated,
    logout: vi.fn(),
  }),
  useTheme: () => 'light',
  useLanguage: () => 'en',
}));

// Mock store
vi.mock('@/store', () => ({
  useAppStore: {
    getState: () => ({
      setTheme: vi.fn(),
      setLanguage: vi.fn(),
      toggleMobileSidebar: vi.fn(),
    }),
  },
}));

// Mock i18n
vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      unreadAlerts: 'Unread Alerts',
      help: 'Help',
      toggleTheme: 'Toggle Theme',
      settings: 'Settings',
      logout: 'Logout',
      login: 'Login',
      english: 'English',
      chinese: 'Chinese',
      japanese: 'Japanese',
      korean: 'Korean',
    };
    return translations[key] || key;
  },
  setLanguage: vi.fn(),
}));

// Mock API
vi.mock('@/api', () => ({
  alertsApi: {
    getUnreadCount: (...args: unknown[]) => mockGetUnreadCount(...args),
  },
}));

// Mock common components (pass-through for CountBadge)
vi.mock('@/components/common', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('@/components/common');
  return {
    ...actual,
    UserSettingsModal: () => null,
    Avatar: ({ name }: { name: string }) => <span>{name}</span>,
  };
});

// Mock DocumentViewer
vi.mock('@/components/work/DocumentViewer', () => ({
  DocumentViewer: () => null,
  helpDocs: [],
  getDocTitle: () => '',
}));

// Mock react-router-dom navigate
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

import { Header } from './Header';

describe('Header - Notification Bell', () => {
  beforeEach(() => {
    mockIsAuthenticated = true;
    mockGetUnreadCount.mockResolvedValue(5);
    mockNavigate.mockClear();
    mockGetUnreadCount.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const renderHeader = (compact = false) =>
    render(
      <MemoryRouter>
        <Header compact={compact} />
      </MemoryRouter>
    );

  it('renders notification bell when authenticated', async () => {
    renderHeader();
    await waitFor(() => {
      expect(screen.getByTitle('Unread Alerts')).toBeInTheDocument();
    });
  });

  it('hides notification bell when not authenticated', async () => {
    mockIsAuthenticated = false;
    renderHeader();
    // Give time for any async effects to settle
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(screen.queryByTitle('Unread Alerts')).not.toBeInTheDocument();
  });

  it('calls getUnreadCount on mount', async () => {
    renderHeader();
    await waitFor(() => {
      expect(mockGetUnreadCount).toHaveBeenCalled();
    });
  });

  it('navigates to /manage/quota when bell is clicked', async () => {
    renderHeader();
    await waitFor(() => {
      expect(screen.getByTitle('Unread Alerts')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTitle('Unread Alerts'));
    expect(mockNavigate).toHaveBeenCalledWith('/manage/quota');
  });

  it('handles API error gracefully without crashing', async () => {
    mockGetUnreadCount.mockRejectedValue(new Error('Network error'));
    renderHeader();

    // Wait for the fetch attempt
    await act(async () => {
      await new Promise((r) => setTimeout(r, 200));
    });

    // Bell should still be visible
    expect(screen.getByTitle('Unread Alerts')).toBeInTheDocument();
  });

  it('renders bell in compact mode when authenticated', async () => {
    renderHeader(true);
    await waitFor(() => {
      expect(screen.getByTitle('Unread Alerts')).toBeInTheDocument();
    });
  });

  it('polls every 30 seconds using fake timers', async () => {
    vi.useFakeTimers();
    mockGetUnreadCount.mockClear();

    renderHeader();

    // Initial fetch
    await act(async () => {
      vi.advanceTimersByTime(0);
    });

    const initialCallCount = mockGetUnreadCount.mock.calls.length;
    expect(initialCallCount).toBeGreaterThanOrEqual(1);

    // Advance 30 seconds
    await act(async () => {
      vi.advanceTimersByTime(30000);
    });

    expect(mockGetUnreadCount.mock.calls.length).toBeGreaterThan(initialCallCount);

    vi.useRealTimers();
  });

  it('cleans up polling on unmount', async () => {
    vi.useFakeTimers();

    const { unmount } = renderHeader();

    await act(async () => {
      vi.advanceTimersByTime(0);
    });

    const callsBeforeUnmount = mockGetUnreadCount.mock.calls.length;

    unmount();

    // Advance time after unmount
    await act(async () => {
      vi.advanceTimersByTime(60000);
    });

    // Should not have been called again after unmount
    expect(mockGetUnreadCount.mock.calls.length).toBe(callsBeforeUnmount);

    vi.useRealTimers();
  });
});
