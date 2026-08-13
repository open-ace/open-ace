/**
 * Category Conflict Detection Utilities
 *
 * Issue #2572: Detect keyword conflicts between categories
 */

import type { ProjectCategory } from '@/api/projectCategories';
import type { ProjectStats } from '@/api/projects';

export interface CategoryConflict {
  projectId: number;
  projectName: string;
  projectPath: string;
  matchedCategories: string[];
}

/**
 * Normalize path separators for cross-platform matching
 */
export function normalizePath(path: string): string {
  return path.replace(/\\/g, '/');
}

/**
 * Check if a project path matches any pattern in the list
 */
export function matchesPatterns(projectPath: string, patterns: string[]): boolean {
  const normalizedPath = normalizePath(projectPath).toLowerCase();
  return patterns.some((p) => p && normalizedPath.includes(normalizePath(p).toLowerCase()));
}

/**
 * Detect projects that match multiple categories
 */
export function detectCategoryConflicts(
  categories: ProjectCategory[],
  stats: ProjectStats[]
): CategoryConflict[] {
  const conflicts: CategoryConflict[] = [];

  // Only check active categories sorted by sort_order
  const activeCategories = categories
    .filter((c) => c.is_active)
    .sort((a, b) => a.sort_order - b.sort_order);

  for (const stat of stats) {
    const matchedCategories: string[] = [];

    for (const category of activeCategories) {
      if (matchesPatterns(stat.project_path, category.key_patterns)) {
        matchedCategories.push(category.name);
      }
    }

    // If project matches more than one category, it's a conflict
    if (matchedCategories.length > 1) {
      conflicts.push({
        projectId: stat.project_id,
        projectName: stat.project_name ?? stat.project_path.split(/[/\\]/).pop() ?? 'Unknown',
        projectPath: stat.project_path,
        matchedCategories,
      });
    }
  }

  return conflicts;
}

/**
 * Calculate impact preview for category keyword changes
 */
export function calculateImpactPreview(
  category: ProjectCategory,
  newPatterns: string[],
  stats: ProjectStats[]
): {
  added: ProjectStats[];
  removed: ProjectStats[];
  currentCount: number;
  newCount: number;
} {
  const currentMatches = stats.filter((s) =>
    matchesPatterns(s.project_path, category.key_patterns)
  );
  const newMatches = stats.filter((s) => matchesPatterns(s.project_path, newPatterns));

  const currentIds = new Set(currentMatches.map((s) => s.project_id));
  const newIds = new Set(newMatches.map((s) => s.project_id));

  const added = newMatches.filter((s) => !currentIds.has(s.project_id));
  const removed = currentMatches.filter((s) => !newIds.has(s.project_id));

  return {
    added,
    removed,
    currentCount: currentMatches.length,
    newCount: newMatches.length,
  };
}