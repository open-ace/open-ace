/**
 * Feature Flags API - Get current state of all configurable features.
 *
 * Used by frontend to dynamically control UI elements based on feature availability.
 * Mirrors the backend feature_flags_bp endpoint.
 */

import { apiClient } from './client';

export interface FeatureFlags {
  model_gateway: boolean;
  run_timeline: boolean;
  policy: boolean;
  autonomous: boolean;
}

export const featureFlagsApi = {
  async getFlags(): Promise<FeatureFlags> {
    const response = await apiClient.get<FeatureFlags>('/api/feature-flags');
    return response;
  },
};