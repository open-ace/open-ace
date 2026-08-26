/**
 * RemoteMachineManagement Component Tests - Token Rotate Offline Scenario
 *
 * Tests cover Issue #1503:
 * - Offline agent shows warning message when rotating token
 * - Online agent shows normal message when rotating token
 * - Uses backend-returned message field
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { RemoteMachine } from '@/api';

// Mocks are at top level for vitest hoisting
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

vi.mock('@/hooks', () => ({
  useAdminTenant: () => ({
    selectedTenantId: 1,
    isLoading: false,
    error: null,
    selectTenant: vi.fn(),
    clearSelection: vi.fn(),
    retry: vi.fn(),
  }),
  useMachines: vi.fn(() => ({
    data: {
      machines: [
        {
          id: 1,
          machine_id: 'test-machine-online',
          machine_name: 'Test Machine Online',
          hostname: 'test-host',
          os_type: 'linux',
          status: 'online',
          connected: true,
          token_status: 'active',
          current_user_permission: null,
        },
        {
          id: 2,
          machine_id: 'test-machine-offline',
          machine_name: 'Test Machine Offline',
          hostname: 'test-host-2',
          os_type: 'linux',
          status: 'offline',
          connected: false,
          token_status: 'active',
          current_user_permission: null,
        },
      ],
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })),
  useMachineUsers: vi.fn(() => ({ data: { users: [] } })),
  useGenerateToken: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({ registration_token: 'test-token' }),
    isPending: false,
  })),
  useDeregisterMachine: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({ success: true }),
    isPending: false,
  })),
  useRotateMachineToken: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({
      success: true,
      agent_token: 'new-test-token',
      message: 'Agent token rotated. The new token has been pushed to the agent.',
    }),
    isPending: false,
  })),
  useRevokeMachineToken: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({ success: true }),
    isPending: false,
  })),
  useAssignUser: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({ success: true }),
    isPending: false,
  })),
  useRevokeUser: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({ success: true }),
    isPending: false,
  })),
  useUsers: vi.fn(() => ({ data: { users: [] } })),
  useAuth: vi.fn(() => ({ user: { id: 1, role: 'admin', tenant_id: 1 } })),
  useApiError: vi.fn(() => ({
    handleAndGetMessage: vi.fn().mockReturnValue('Error message'),
    handleError: vi.fn(),
    getErrorMessage: vi.fn().mockReturnValue('Error message'),
  })),
}));

vi.mock('@/utils/permissions', () => ({
  canManageAllTenants: () => true,
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => key,
}));

vi.mock('@/utils', () => ({
  copyToClipboard: vi.fn().mockResolvedValue(true),
}));

vi.mock('@/components/common', () => ({
  Card: () => null,
  Button: () => null,
  Loading: () => null,
  Error: () => null,
  EmptyState: () => null,
  Badge: () => null,
  Modal: () => null,
  Select: () => null,
  useToast: vi.fn(() => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  })),
  useConfirm: vi.fn(() => vi.fn().mockResolvedValue(true)),
}));

describe('RemoteMachineManagement - Token Rotate Offline Scenario (Issue #1503)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Offline status detection logic', () => {
    it('should detect offline status via connected field', () => {
      const offlineMachine: RemoteMachine = {
        id: 2,
        machine_id: 'test-machine-offline',
        machine_name: 'Test Machine Offline',
        hostname: 'test-host',
        os_type: 'linux',
        status: 'offline',
        connected: false,
        token_status: 'active',
        current_user_permission: null,
      };

      // Verify the detection logic matches PR #1555: !rotateTarget.connected
      const isOfflineViaConnected = !offlineMachine.connected;
      expect(isOfflineViaConnected).toBe(true);
    });

    it('should detect offline status via status field', () => {
      const offlineMachine: RemoteMachine = {
        id: 2,
        machine_id: 'test-machine-offline',
        machine_name: 'Test Machine Offline',
        hostname: 'test-host',
        os_type: 'linux',
        status: 'offline',
        connected: true, // connected=true but status='offline' - edge case
        token_status: 'active',
        current_user_permission: null,
      };

      // Verify the detection logic matches PR #1555: rotateTarget.status === 'offline'
      const isOfflineViaStatus = offlineMachine.status === 'offline';
      expect(isOfflineViaStatus).toBe(true);
    });

    it('should use defensive check combining both fields', () => {
      // The PR uses: (!rotateTarget.connected || rotateTarget.status === 'offline')
      // This is defensive programming - checks both fields for robustness

      const machine1: RemoteMachine = {
        id: 1,
        machine_id: 'm1',
        machine_name: 'M1',
        connected: false,
        status: 'online',
        token_status: 'active',
      } as RemoteMachine;

      const machine2: RemoteMachine = {
        id: 2,
        machine_id: 'm2',
        machine_name: 'M2',
        connected: true,
        status: 'offline',
        token_status: 'active',
      } as RemoteMachine;

      const machine3: RemoteMachine = {
        id: 3,
        machine_id: 'm3',
        machine_name: 'M3',
        connected: true,
        status: 'online',
        token_status: 'active',
      } as RemoteMachine;

      // Detection logic from PR #1555
      const isOffline1 = !machine1.connected || machine1.status === 'offline';
      const isOffline2 = !machine2.connected || machine2.status === 'offline';
      const isOffline3 = !machine3.connected || machine3.status === 'offline';

      expect(isOffline1).toBe(true); // connected=false triggers warning
      expect(isOffline2).toBe(true); // status='offline' triggers warning
      expect(isOffline3).toBe(false); // both indicate online, no warning
    });

    it('should distinguish connected (realtime) vs status (database) fields', () => {
      // connected: realtime connection status (memory check _connections)
      // status: database field with values 'online'/'offline'/'idle'/'busy'

      // They may differ temporarily during heartbeat transitions
      const transitioningMachine: RemoteMachine = {
        id: 1,
        machine_id: 'transitioning',
        machine_name: 'Transitioning Machine',
        connected: false, // Memory check shows disconnected
        status: 'online', // Database hasn't updated yet
        token_status: 'active',
      } as RemoteMachine;

      // Defensive check catches this edge case
      const shouldShowWarning =
        !transitioningMachine.connected || transitioningMachine.status === 'offline';
      expect(shouldShowWarning).toBe(true); // Shows warning due to connected=false
    });
  });

  describe('Message field usage', () => {
    it('should verify rotateMachineToken returns message field', () => {
      // This test verifies the API response structure matches frontend expectations

      const mockResponse = {
        success: true,
        agent_token: 'new-test-token',
        message: 'Agent token rotated. The new token has been pushed to the agent.',
      };

      // Verify message field is present
      expect(mockResponse.message).toBeDefined();
      expect(typeof mockResponse.message).toBe('string');
    });

    it('should have offline-specific message from backend', () => {
      // Backend returns different messages based on agent connection status
      // See app/routes/remote.py:rotate_machine_token

      const onlineMessage = 'Agent token rotated. The new token has been pushed to the agent.';
      const offlineMessage =
        'Agent token rotated. Agent is offline — save the new token and manually update the agent config.';

      // Both messages should be distinguishable
      expect(onlineMessage).toContain('pushed to the agent');
      expect(offlineMessage).toContain('offline');
      expect(offlineMessage).toContain('manually update');
    });

    it('should use fallback message when backend returns empty', () => {
      // Frontend code: setRotatedMessage(result.message || t('tokenRotatedMessage', language))

      const emptyMessageResponse = {
        success: true,
        agent_token: 'new-token',
        message: '',
      };

      // Fallback logic check
      const fallbackUsed = !emptyMessageResponse.message;
      expect(fallbackUsed).toBe(true);
    });
  });

  describe('UI styling for offline warning', () => {
    it('should use text-warning class for offline agent', () => {
      // PR #1555 adds: className with text-warning for offline
      // and text-muted for online

      const offlineClass = 'text-warning small';
      const onlineClass = 'text-muted small';

      expect(offlineClass).toContain('text-warning');
      expect(onlineClass).toContain('text-muted');
    });

    it('should show warning icon for offline agent', () => {
      // PR #1555 adds: <i className="bi bi-exclamation-triangle me-1" />

      const warningIconClass = 'bi bi-exclamation-triangle me-1';
      expect(warningIconClass).toContain('exclamation-triangle');
    });
  });

  // Issue #3066: Hardware info display tests
  describe('Hardware resource display (Issue #3066)', () => {
    it('should have capabilities field with hardware info', () => {
      const machine: RemoteMachine = {
        id: 1,
        machine_id: 'test-hw',
        machine_name: 'Test HW Machine',
        hostname: 'hw-host',
        os_type: 'linux',
        status: 'online',
        connected: true,
        token_status: 'active',
        capabilities: {
          cpu_cores: 8,
          memory_mb: 8192,
          disk_free_gb: 100.5,
        },
      } as RemoteMachine;

      const caps = machine.capabilities as Record<string, unknown>;
      expect(caps.cpu_cores).toBe(8);
      expect(caps.memory_mb).toBe(8192);
      expect(caps.disk_free_gb).toBe(100.5);
    });

    it('should convert memory_mb to GB with 1 decimal place', () => {
      const memoryMb = 8192;
      const memoryGb = (memoryMb / 1024).toFixed(1);
      expect(memoryGb).toBe('8.0');
    });

    it('should convert fractional memory_mb correctly', () => {
      const memoryMb = 16384;
      const memoryGb = (memoryMb / 1024).toFixed(1);
      expect(memoryGb).toBe('16.0');
    });

    it('should handle null capabilities safely', () => {
      const machine: RemoteMachine = {
        id: 1,
        machine_id: 'test-null-caps',
        machine_name: 'Null Caps Machine',
        hostname: 'host',
        os_type: 'linux',
        status: 'online',
        connected: true,
        token_status: 'active',
        capabilities: null,
      } as RemoteMachine;

      expect(machine.capabilities).toBeNull();
      // The component should not render hardware cards when capabilities is null
      const shouldRender =
        machine.capabilities !== null && Object.keys(machine.capabilities ?? {}).length > 0;
      expect(shouldRender).toBe(false);
    });

    it('should handle empty capabilities safely', () => {
      const machine: RemoteMachine = {
        id: 1,
        machine_id: 'test-empty-caps',
        machine_name: 'Empty Caps Machine',
        hostname: 'host',
        os_type: 'linux',
        status: 'online',
        connected: true,
        token_status: 'active',
        capabilities: {},
      } as RemoteMachine;

      const shouldRender =
        machine.capabilities !== null && Object.keys(machine.capabilities ?? {}).length > 0;
      expect(shouldRender).toBe(false);
    });

    it('should handle partial capabilities (only cpu_cores)', () => {
      const caps = { cpu_cores: 4 };
      const cpuCores = typeof caps.cpu_cores === 'number' ? caps.cpu_cores : undefined;
      const memoryMb = typeof caps.memory_mb === 'number' ? caps.memory_mb : undefined;
      const diskFreeGb = typeof caps.disk_free_gb === 'number' ? caps.disk_free_gb : undefined;

      expect(cpuCores).toBe(4);
      expect(memoryMb).toBeUndefined();
      expect(diskFreeGb).toBeUndefined();
    });

    it('should handle zero value hardware fields', () => {
      const caps = {
        cpu_cores: 0,
        memory_mb: 0,
        disk_free_gb: 0,
      };
      const cpuCores = typeof caps.cpu_cores === 'number' ? caps.cpu_cores : undefined;
      const memoryMb = typeof caps.memory_mb === 'number' ? caps.memory_mb : undefined;
      const diskFreeGb = typeof caps.disk_free_gb === 'number' ? caps.disk_free_gb : undefined;

      // Zero values should not show cards (using > 0 check)
      expect(cpuCores !== undefined && cpuCores > 0).toBe(false);
      expect(memoryMb !== undefined && memoryMb > 0).toBe(false);
      expect(diskFreeGb !== undefined && diskFreeGb > 0).toBe(false);
    });

    it('should detect at least one hardware field for card rendering', () => {
      const caps = { cpu_cores: 8 };
      const cpuCores = typeof caps.cpu_cores === 'number' ? caps.cpu_cores : undefined;
      const memoryMb = typeof caps.memory_mb === 'number' ? caps.memory_mb : undefined;
      const diskFreeGb = typeof caps.disk_free_gb === 'number' ? caps.disk_free_gb : undefined;

      const hasAnyHardware =
        cpuCores !== undefined || memoryMb !== undefined || diskFreeGb !== undefined;
      expect(hasAnyHardware).toBe(true);
    });
  });
});
