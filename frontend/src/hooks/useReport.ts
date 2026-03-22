/**
 * Report Hooks - Custom hooks for report operations
 */

import { useQuery } from '@tanstack/react-query';
import { reportApi } from '@/api';

export function useMyUsage(startDate?: string, endDate?: string) {
  return useQuery({
    queryKey: ['report', 'my-usage', startDate, endDate],
    queryFn: () => reportApi.getMyUsage(startDate, endDate),
  });
}
