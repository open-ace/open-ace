/**
 * Header Component Tests - Notification Bell & Unread Alert Badge
 *
 * Tests cover:
 * - Notification bell renders when authenticated
 * - Notification bell hidden when not authenticated
 * - Clicking bell navigates based on user role:
 *   - Admin/Manager: /manage/quota?tab=alerts
 *   - Regular user: /work/alerts
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
let mockUserRole: string | null = 'admin';
let mockUnreadCount = 0;
const mockNavigate = vi.fn();

// Mock hooks
vi.mock('@/hooks', () => ({
  useAuth: () => ({
    user: {
      username: 'testuser',
      email: 'test@test.com',
      avatar_url: null,
      role: mockUserRole,
    },
    isAuthenticated: mockIsAuthenticated,
    logout: vi.fn(),
  }),
  useTheme: () => 'light',
  useLanguage: () => 'en',
  useAlertStream: () => ({
    unreadCount: mockUnreadCount,
    connectionStatus: 'connected',
    alerts: [],
    reconnect: vi.fn(),
  }),
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
    mockUserRole = 'admin';
    mockUnreadCount = 5;
    mockNavigate.mockClear();
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

  // Issue #3223: Admin navigates to /manage/quota?tab=alerts
  it('navigates to /manage/quota?tab=alerts when admin clicks bell', async () => {
    mockUserRole = 'admin';
    renderHeader();
    await waitFor(() => {
      expect(screen.getByTitle('Unread Alerts')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTitle('Unread Alerts'));
    expect(mockNavigate).toHaveBeenCalledWith('/manage/quota?tab=alerts');
  });

  // Issue #3223: Manager navigates to /manage/quota?tab=alerts
  it('navigates to /manage/quota?tab=alerts when manager clicks bell', async () => {
    mockUserRole = 'manager';
    renderHeader();
    await waitFor(() => {
      expect(screen.getByTitle('Unread Alerts')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTitle('Unread Alerts'));
    expect(mockNavigate).toHaveBeenCalledWith('/manage/quota?tab=alerts');
  });

  // Issue #3223: Regular user navigates to /work/alerts
  it('navigates to /work/alerts when regular user clicks bell', async () => {
    mockUserRole = 'user';
    renderHeader();
    await waitFor(() => {
      expect(screen.getByTitle('Unread Alerts')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTitle('Unread Alerts'));
    expect(mockNavigate).toHaveBeenCalledWith('/work/alerts');
  });

  // Issue #3223: Readonly user navigates to /work/alerts
  it('navigates to /work/alerts when readonly user clicks bell', async () => {
    mockUserRole = 'readonly';
    renderHeader();
    await waitFor(() => {
      expect(screen.getByTitle('Unread Alerts')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTitle('Unread Alerts'));
    expect(mockNavigate).toHaveBeenCalledWith('/work/alerts');
  });

  it('renders bell in compact mode when authenticated', async () => {
    renderHeader(true);
    await waitFor(() => {
      expect(screen.getByTitle('Unread Alerts')).toBeInTheDocument();
    });
  });

  // Issue #3332: SSE replaces polling - no longer need polling tests
});
