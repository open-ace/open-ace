/**
 * CategoryFilter Component - Filter projects by category
 *
 * Issue #2572: Project category filter dropdown
 */

import React from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Select } from '@/components/common';
import type { ProjectCategory } from '@/api/projectCategories';

interface CategoryFilterProps {
  categories: ProjectCategory[];
  value: number | 'all';
  onChange: (value: number | 'all') => void;
  className?: string;
}

/**
 * Dropdown filter for selecting project category
 */
export const CategoryFilter: React.FC<CategoryFilterProps> = ({
  categories,
  value,
  onChange,
  className,
}) => {
  const language = useLanguage();

  // Build options: all categories + each category
  const options = [
    { value: 'all', label: t('allCategories', language) },
    ...categories
      .filter((c) => c.is_active)
      .sort((a, b) => a.sort_order - b.sort_order)
      .map((c) => ({
        value: c.id.toString(),
        label: c.name,
      })),
  ];

  const handleChange = (stringValue: string) => {
    if (stringValue === 'all') {
      onChange('all');
    } else {
      const numValue = parseInt(stringValue, 10);
      onChange(isNaN(numValue) ? 'all' : numValue);
    }
  };

  return (
    <div className={className}>
      <label className="form-label">{t('filterByCategory', language)}</label>
      <Select
        options={options}
        value={value === 'all' ? 'all' : value.toString()}
        onChange={handleChange}
      />
    </div>
  );
};

export default CategoryFilter;
