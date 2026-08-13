/**
 * CategoryEditModal Component - Create/Edit project category form
 *
 * Issue #2572: Project category management UI
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Modal, Button, TextInput, Switch } from '@/components/common';
import { MultiValueInput } from '@/components/common/MultiValueInput';
import {
  createProjectCategory,
  updateProjectCategory,
  type ProjectCategory,
} from '@/api/projectCategories';
import { useToast } from '@/components/common';

interface CategoryEditModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  category?: ProjectCategory | null; // null for create mode
}

interface FormData {
  name: string;
  key_patterns: string[];
  sort_order: number;
  is_active: boolean;
}

interface FormErrors {
  name?: string;
  key_patterns?: string;
  sort_order?: string;
}

/**
 * Modal for creating or editing a project category
 */
export const CategoryEditModal: React.FC<CategoryEditModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
  category,
}) => {
  const language = useLanguage();
  const toast = useToast();
  const isEditMode = category !== null && category !== undefined;

  const [formData, setFormData] = useState<FormData>({
    name: '',
    key_patterns: [],
    sort_order: 0,
    is_active: true,
  });
  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Initialize form data when category changes
  useEffect(() => {
    if (category) {
      setFormData({
        name: category.name || '',
        key_patterns: category.key_patterns || [],
        sort_order: category.sort_order || 0,
        is_active: category.is_active ?? true,
      });
    } else {
      setFormData({
        name: '',
        key_patterns: [],
        sort_order: 0,
        is_active: true,
      });
    }
    setErrors({});
  }, [category, isOpen]);

  // Validate form
  const validate = useCallback((): boolean => {
    const newErrors: FormErrors = {};

    // Name validation
    if (!formData.name.trim()) {
      newErrors.name = t('categoryNameRequired', language);
    } else if (formData.name.length > 64) {
      newErrors.name = t('categoryNameMaxLength', language);
    }

    // Key patterns validation
    const emptyPatterns = formData.key_patterns.filter((p) => p.trim() === '');
    if (emptyPatterns.length > 0) {
      newErrors.key_patterns = t('patternNotEmpty', language);
    }
    const tooLongPatterns = formData.key_patterns.filter((p) => p.length > 128);
    if (tooLongPatterns.length > 0) {
      newErrors.key_patterns = t('patternMaxLength', language);
    }

    // Sort order validation
    if (formData.sort_order < 0 || formData.sort_order > 999) {
      newErrors.sort_order = t('sortOrderRange', language);
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [formData, language]);

  // Handle submit
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validate()) {
      return;
    }

    setIsSubmitting(true);
    try {
      if (isEditMode && category) {
        // Update existing category
        await updateProjectCategory(category.id, {
          name: formData.name.trim(),
          key_patterns: formData.key_patterns.filter((p) => p.trim() !== ''),
          sort_order: formData.sort_order,
          is_active: formData.is_active,
        });
        toast.success(t('categoryUpdated', language));
      } else {
        // Create new category
        await createProjectCategory({
          name: formData.name.trim(),
          key_patterns: formData.key_patterns.filter((p) => p.trim() !== ''),
          sort_order: formData.sort_order,
        });
        toast.success(t('categoryCreated', language));
      }
      onSuccess();
      onClose();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to save category';
      toast.error(errorMessage);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Handle key patterns change
  const handleKeyPatternsChange = useCallback((value: string[]) => {
    setFormData((prev) => ({ ...prev, key_patterns: value }));
    // Clear error when user starts typing
    if (errors.key_patterns) {
      setErrors((prev) => ({ ...prev, key_patterns: undefined }));
    }
  }, [errors.key_patterns]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={isEditMode ? t('editCategory', language) : t('createCategory', language)}
      size="md"
    >
      <form onSubmit={handleSubmit}>
        {/* Category Name */}
        <TextInput
          label={t('categoryName', language)}
          value={formData.name}
          onChange={(value) => {
            // Enforce max length
            const truncated = value.slice(0, 64);
            setFormData((prev) => ({ ...prev, name: truncated }));
            if (errors.name) {
              setErrors((prev) => ({ ...prev, name: undefined }));
            }
          }}
          error={errors.name}
          required
          placeholder={t('categoryNamePlaceholder', language)}
        />

        {/* Match Patterns */}
        <MultiValueInput
          label={t('matchPatterns', language)}
          value={formData.key_patterns}
          onChange={handleKeyPatternsChange}
          placeholder={t('patternPlaceholder', language)}
          hint={t('patternHint', language)}
          error={errors.key_patterns}
          maxLength={128}
          maxItems={20}
        />

        {/* Sort Order */}
        <TextInput
          type="number"
          label={t('sortOrder', language)}
          value={formData.sort_order.toString()}
          onChange={(value) => {
            const numValue = parseInt(value, 10);
            setFormData((prev) => ({ ...prev, sort_order: isNaN(numValue) ? 0 : numValue }));
            if (errors.sort_order) {
              setErrors((prev) => ({ ...prev, sort_order: undefined }));
            }
          }}
          error={errors.sort_order}
          hint={t('sortOrderHint', language)}
        />

        {/* Active status - only show in edit mode */}
        {isEditMode && (
          <div className="mb-3">
            <Switch
              label={t('categoryActive', language)}
              checked={formData.is_active}
              onChange={(checked) => setFormData((prev) => ({ ...prev, is_active: checked }))}
            />
          </div>
        )}

        {/* Pattern Preview */}
        {formData.key_patterns.length > 0 && formData.key_patterns.some((p) => p.trim()) && (
          <div className="alert alert-info py-2 px-3">
            <small>
              <i className="bi bi-info-circle me-1" />
              {t('patternPreview', language)}:{' '}
              <strong>
                {formData.key_patterns
                  .filter((p) => p.trim())
                  .map((p) => `"${p}"`)
                  .join(', ')}
              </strong>
            </small>
          </div>
        )}

        {/* Footer */}
        <div className="modal-footer border-0 px-0 pb-0">
          <Button variant="secondary" onClick={onClose} disabled={isSubmitting}>
            {t('cancel', language)}
          </Button>
          <Button type="submit" variant="primary" loading={isSubmitting}>
            {isEditMode ? t('save', language) : t('createCategory', language)}
          </Button>
        </div>
      </form>
    </Modal>
  );
};

export default CategoryEditModal;