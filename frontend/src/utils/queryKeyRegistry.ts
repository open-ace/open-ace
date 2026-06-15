/**
 * Query Key Registry - Central registry for all query keys
 *
 * Purpose:
 * - Ensure consistent naming conventions
 * - Track refresh scopes
 * - Provide query key lookup utilities
 *
 * Naming format: ['page', 'subpage', 'entity', 'filter?']
 */

import type { QueryKey } from '@tanstack/react-query';

/**
 * RefreshScope - Defines how query keys are refreshed
 */
export type RefreshScope = 'page' | 'global' | 'none';

/**
 * QueryKeyRegistration - Registration config for a query key
 */
export interface QueryKeyRegistration {
  key: QueryKey;
  page: string; // Route path or page identifier
  description: string;
  refreshScope: RefreshScope;
}

/**
 * Query Key Registry
 */
class QueryKeyRegistry {
  private registrations: Map<string, QueryKeyRegistration> = new Map();

  /**
   * Register a query key
   */
  register(config: QueryKeyRegistration): void {
    const hash = this.hashKey(config.key);
    this.registrations.set(hash, config);
  }

  /**
   * Batch register multiple query keys
   */
  registerBatch(configs: QueryKeyRegistration[]): void {
    configs.forEach((config) => this.register(config));
  }

  /**
   * Get registration by query key
   */
  getRegistration(key: QueryKey): QueryKeyRegistration | undefined {
    const hash = this.hashKey(key);
    return this.registrations.get(hash);
  }

  /**
   * Get all query keys for a page
   */
  getKeysByPage(page: string): QueryKey[] {
    return Array.from(this.registrations.values())
      .filter((reg) => reg.page === page)
      .map((reg) => reg.key);
  }

  /**
   * Get all query keys with a specific prefix
   */
  getKeysByPrefix(prefix: QueryKey): QueryKey[] {
    return Array.from(this.registrations.values())
      .filter((reg) => {
        // Check if registration key starts with prefix
        if (reg.key.length < prefix.length) {
          return false;
        }
        return prefix.every((item, index) => {
          const prefixItem = item;
          const keyItem = reg.key[index];
          if (typeof prefixItem !== 'object' && typeof keyItem !== 'object') {
            return prefixItem === keyItem;
          }
          return JSON.stringify(prefixItem) === JSON.stringify(keyItem);
        });
      })
      .map((reg) => reg.key);
  }

  /**
   * Get all query keys with page refresh scope
   */
  getPageRefreshKeys(): QueryKey[] {
    return Array.from(this.registrations.values())
      .filter((reg) => reg.refreshScope === 'page')
      .map((reg) => reg.key);
  }

  /**
   * Validate a query key against naming conventions
   */
  validateQueryKey(key: QueryKey): boolean {
    // Basic validation: must be an array with at least one element
    if (!Array.isArray(key) || key.length === 0) {
      return false;
    }

    // First element should be a string (page identifier)
    if (typeof key[0] !== 'string') {
      return false;
    }

    return true;
  }

  /**
   * Get all registrations
   */
  getAllRegistrations(): QueryKeyRegistration[] {
    return Array.from(this.registrations.values());
  }

  /**
   * Clear all registrations (for testing)
   */
  clear(): void {
    this.registrations.clear();
  }

  /**
   * Hash a query key for storage
   */
  private hashKey(key: QueryKey): string {
    return JSON.stringify(key);
  }
}

/**
 * Global registry instance
 */
export const queryKeyRegistry = new QueryKeyRegistry();

/**
 * Initialize registry with standard query keys
 */
export function initializeQueryKeyRegistry(): void {
  // Dashboard page
  queryKeyRegistry.registerBatch([
    {
      key: ['dashboard', 'today'],
      page: '/manage/dashboard',
      description: 'Today usage data',
      refreshScope: 'page',
    },
    {
      key: ['dashboard', 'summary'],
      page: '/manage/dashboard',
      description: 'Summary statistics',
      refreshScope: 'page',
    },
    {
      key: ['dashboard', 'hosts'],
      page: '/manage/dashboard',
      description: 'Host list',
      refreshScope: 'page',
    },
    {
      key: ['dashboard', 'trend'],
      page: '/manage/dashboard',
      description: 'Trend data',
      refreshScope: 'page',
    },
  ]);

  // Request Dashboard page
  queryKeyRegistry.registerBatch([
    {
      key: ['analysis', 'request-dashboard', 'stats'],
      page: '/manage/analysis/request-dashboard',
      description: 'Request statistics',
      refreshScope: 'page',
    },
    {
      key: ['analysis', 'request-dashboard', 'requests'],
      page: '/manage/analysis/request-dashboard',
      description: 'Request list',
      refreshScope: 'page',
    },
  ]);

  // Messages page
  queryKeyRegistry.registerBatch([
    {
      key: ['messages', 'list'],
      page: '/manage/messages',
      description: 'Messages list',
      refreshScope: 'page',
    },
  ]);

  // Trend Analysis page
  queryKeyRegistry.registerBatch([
    {
      key: ['analysis', 'trend'],
      page: '/manage/analysis/trend',
      description: 'Trend analysis data',
      refreshScope: 'page',
    },
  ]);

  // Anomaly Detection page
  queryKeyRegistry.registerBatch([
    {
      key: ['analysis', 'anomaly'],
      page: '/manage/analysis/anomaly',
      description: 'Anomaly detection results',
      refreshScope: 'page',
    },
  ]);

  // ROI Analysis page
  queryKeyRegistry.registerBatch([
    {
      key: ['analysis', 'roi'],
      page: '/manage/analysis/roi',
      description: 'ROI analysis data',
      refreshScope: 'page',
    },
  ]);

  // Conversation History page
  queryKeyRegistry.registerBatch([
    {
      key: ['conversation-history'],
      page: '/manage/analysis/conversation-history',
      description: 'Conversation history',
      refreshScope: 'page',
    },
  ]);

  // Audit Center page
  queryKeyRegistry.registerBatch([
    {
      key: ['audit'],
      page: '/manage/audit',
      description: 'Audit logs',
      refreshScope: 'page',
    },
  ]);

  // Quota & Alerts page
  queryKeyRegistry.registerBatch([
    {
      key: ['quota'],
      page: '/manage/quota',
      description: 'Quota data',
      refreshScope: 'page',
    },
    {
      key: ['alerts'],
      page: '/manage/quota',
      description: 'Alerts data',
      refreshScope: 'page',
    },
  ]);

  // Compliance page
  queryKeyRegistry.registerBatch([
    {
      key: ['compliance'],
      page: '/manage/compliance',
      description: 'Compliance data',
      refreshScope: 'page',
    },
  ]);

  // Security Center page
  queryKeyRegistry.registerBatch([
    {
      key: ['security'],
      page: '/manage/security',
      description: 'Security data',
      refreshScope: 'page',
    },
  ]);

  // User Management page
  queryKeyRegistry.registerBatch([
    {
      key: ['users'],
      page: '/manage/users',
      description: 'User list',
      refreshScope: 'page',
    },
  ]);

  // Tenant Management page
  queryKeyRegistry.registerBatch([
    {
      key: ['tenants'],
      page: '/manage/tenants',
      description: 'Tenant list',
      refreshScope: 'page',
    },
  ]);

  // Project Management page
  queryKeyRegistry.registerBatch([
    {
      key: ['projects'],
      page: '/manage/projects',
      description: 'Project list',
      refreshScope: 'page',
    },
  ]);

  // Remote Machines page - exclude from page refresh (has own refetchInterval)
  queryKeyRegistry.registerBatch([
    {
      key: ['remote', 'sessions'],
      page: '/manage/remote/machines',
      description: 'Remote sessions',
      refreshScope: 'none', // Managed by useRemoteSession hook
    },
    {
      key: ['remote', 'machines'],
      page: '/manage/remote/machines',
      description: 'Remote machines list',
      refreshScope: 'page',
    },
  ]);

  // API Keys page
  queryKeyRegistry.registerBatch([
    {
      key: ['api-keys'],
      page: '/manage/settings/api-keys',
      description: 'API keys list',
      refreshScope: 'page',
    },
  ]);

  // Autonomous work mode - exclude from manage mode refresh
  queryKeyRegistry.registerBatch([
    {
      key: ['autonomous', 'status'],
      page: '/work/autonomous',
      description: 'Autonomous mode status',
      refreshScope: 'none', // Managed by useAutonomous hook
    },
  ]);
}

/**
 * Helper functions to create standard query keys
 */
export const queryKeys = {
  // Dashboard
  dashboard: {
    today: (tool?: string, host?: string) => ['dashboard', 'today', { tool, host }],
    summary: (host?: string, startDate?: string, endDate?: string) => [
      'dashboard',
      'summary',
      { host, startDate, endDate },
    ],
    hosts: () => ['dashboard', 'hosts'],
    trend: (startDate: string, endDate: string, host?: string) => [
      'dashboard',
      'trend',
      { startDate, endDate, host },
    ],
  },

  // Analysis
  analysis: {
    requestDashboard: {
      stats: () => ['analysis', 'request-dashboard', 'stats'],
      requests: () => ['analysis', 'request-dashboard', 'requests'],
    },
    trend: () => ['analysis', 'trend'],
    anomaly: () => ['analysis', 'anomaly'],
    roi: () => ['analysis', 'roi'],
  },

  // Messages
  messages: {
    list: () => ['messages', 'list'],
  },

  // Users
  users: {
    list: () => ['users'],
  },

  // Tenants
  tenants: {
    list: () => ['tenants'],
  },

  // Projects
  projects: {
    list: () => ['projects'],
  },

  // Remote
  remote: {
    machines: () => ['remote', 'machines'],
    sessions: (machineId?: string) => ['remote', 'sessions', { machineId }],
  },

  // Audit
  audit: {
    logs: () => ['audit'],
  },

  // Security
  security: {
    data: () => ['security'],
  },

  // Quota
  quota: {
    usage: () => ['quota'],
  },

  // Alerts
  alerts: {
    list: () => ['alerts'],
  },

  // API Keys
  apiKeys: {
    list: () => ['api-keys'],
  },
};
