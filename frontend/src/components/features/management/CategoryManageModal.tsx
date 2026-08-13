/**
 * CategoryManageModal Component - Project category list management
 *
 * Issue #2572: Project category management UI
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Modal, Button, Badge, EmptyState, ConfirmModal } from '@/components/common';
import { CategoryEditModal } from './CategoryEditModal';
import {
  listProjectCategories,
  updateProjectCategory,
  type ProjectCategory,
} from '@/api/projectCategories';
import { useToast } from '@/components/common';

interface CategoryManageModalProps {
  isOpen: boolean;
  onClose: () => void;
  onChange: () => void; // Callback when categories change
}

/**
 * Modal for managing project categories (list, create, edit, activate/deactivate)
 */
export const CategoryManageModal: React.FC<CategoryManageModalProps> = ({
  isOpen,
  onClose,
  onChange,
}) => {
  const language = useLanguage();
  const toast = useToast();

  const [categories, setCategories] = useState<ProjectCategory[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [editCategory, setEditCategory] = useState<ProjectCategory | null | undefined>(undefined);
  const [showEditModal, setShowEditModal] = useState(false);
  const [deactivateTarget, setDeactivateTarget] = useState<ProjectCategory | null>(null);
  const [activateTarget, setActivateTarget] = useState<ProjectCategory | null>(null);
  const [isToggling, setIsToggling] = useState(false);

  // Fetch categories
  const fetchCategories = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await listProjectCategories();
      // Sort by sort_order
      const sorted = (response.categories || []).sort((a, b) => a.sort_order - b.sort_order);
      setCategories(sorted);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load categories';
      toast.error(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (isOpen) {
      fetchCategories();
    }
  }, [isOpen, fetchCategories]);

  // Handle create new category
  const handleCreate = () => {
    setEditCategory(null); // null means create mode
    setShowEditModal(true);
  };

  // Handle edit category
  const handleEdit = (category: ProjectCategory) => {
    setEditCategory(category);
    setShowEditModal(true);
  };

  // Handle edit success
  const handleEditSuccess = () => {
    fetchCategories();
    onChange();
  };

  // Handle toggle active status
  const handleToggleActive = async (category: ProjectCategory, activate: boolean) => {
    setIsToggling(true);
    try {
      await updateProjectCategory(category.id, { is_active: activate });
      toast.success(
        activate ? t('categoryActivated', language) : t('categoryDeactivated', language)
      );
      fetchCategories();
      onChange();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to update category';
      toast.error(errorMessage);
    } finally {
      setIsToggling(false);
      setDeactivateTarget(null);
      setActivateTarget(null);
    }
  };

  return (
    <>
      <Modal isOpen={isOpen} onClose={onClose} title={t('manageCategories', language)} size="lg">
        {/* Header with create button */}
        <div className="d-flex justify-content-between align-items-center mb-3">
          <p className="text-muted mb-0">{t('manageCategoriesDescription', language)}</p>
          <Button variant="primary" size="sm" onClick={handleCreate}>
            <i className="bi bi-plus-lg me-1" />
            {t('createCategory', language)}
          </Button>
        </div>

        {/* Category list */}
        {isLoading ? (
          <div className="text-center py-4">
            <div className="spinner-border text-primary" role="status">
              <span className="visually-hidden">{t('loading', language)}</span>
            </div>
          </div>
        ) : categories.length === 0 ? (
          <EmptyState
            icon="bi-folder-plus"
            title={t('noCategories', language)}
            description={t('noCategoriesDescription', language)}
            action={
              <Button variant="primary" onClick={handleCreate}>
                <i className="bi bi-plus-lg me-1" />
                {t('createCategory', language)}
              </Button>
            }
          />
        ) : (
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('categoryName', language)}</th>
                  <th>{t('matchPatterns', language)}</th>
                  <th style={{ width: '80px' }}>{t('sortOrder', language)}</th>
                  <th style={{ width: '80px' }}>{t('status', language)}</th>
                  <th style={{ width: '120px' }}>{t('tableActions', language)}</th>
                </tr>
              </thead>
              <tbody>
                {categories.map((category) => (
                  <tr key={category.id}>
                    <td>
                      <strong>{category.name}</strong>
                    </td>
                    <td>
                      {category.key_patterns.length > 0 ? (
                        <div className="d-flex flex-wrap gap-1">
                          {category.key_patterns.slice(0, 3).map((pattern, idx) => (
                            <Badge key={idx} variant="secondary" pill>
                              {pattern}
                            </Badge>
                          ))}
                          {category.key_patterns.length > 3 && (
                            <Badge variant="light" pill>
                              +{category.key_patterns.length - 3}
                            </Badge>
                          )}
                        </div>
                      ) : (
                        <span className="text-muted">-</span>
                      )}
                    </td>
                    <td>
                      <span className="badge bg-secondary">{category.sort_order}</span>
                    </td>
                    <td>
                      {category.is_active ? (
                        <Badge variant="success" pill>
                          {t('categoryActive', language)}
                        </Badge>
                      ) : (
                        <Badge variant="secondary" pill>
                          {t('categoryInactive', language)}
                        </Badge>
                      )}
                    </td>
                    <td>
                      <div className="d-flex gap-1">
                        <Button
                          variant="outline-primary"
                          size="sm"
                          onClick={() => handleEdit(category)}
                          title={t('editCategory', language)}
                        >
                          <i className="bi bi-pencil" />
                        </Button>
                        {category.is_active ? (
                          <Button
                            variant="outline-secondary"
                            size="sm"
                            onClick={() => setDeactivateTarget(category)}
                            title={t('deactivateCategory', language)}
                          >
                            <i className="bi bi-toggle-on" />
                          </Button>
                        ) : (
                          <Button
                            variant="outline-success"
                            size="sm"
                            onClick={() => setActivateTarget(category)}
                            title={t('activateCategory', language)}
                          >
                            <i className="bi bi-toggle-off" />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Modal>

      {/* Edit Modal */}
      <CategoryEditModal
        isOpen={showEditModal}
        onClose={() => setShowEditModal(false)}
        onSuccess={handleEditSuccess}
        category={editCategory}
      />

      {/* Deactivate Confirmation */}
      <ConfirmModal
        isOpen={deactivateTarget !== null}
        onClose={() => setDeactivateTarget(null)}
        onConfirm={() => deactivateTarget && handleToggleActive(deactivateTarget, false)}
        title={t('deactivateCategory', language)}
        message={t('deactivateCategoryConfirm', language).replace(
          '{name}',
          deactivateTarget?.name || ''
        )}
        confirmText={t('deactivate', language)}
        cancelText={t('cancel', language)}
        variant="warning"
        loading={isToggling}
      />

      {/* Activate Confirmation */}
      <ConfirmModal
        isOpen={activateTarget !== null}
        onClose={() => setActivateTarget(null)}
        onConfirm={() => activateTarget && handleToggleActive(activateTarget, true)}
        title={t('activateCategory', language)}
        message={t('activateCategoryConfirm', language).replace(
          '{name}',
          activateTarget?.name || ''
        )}
        confirmText={t('activate', language)}
        cancelText={t('cancel', language)}
        variant="primary"
        loading={isToggling}
      />
    </>
  );
};

export default CategoryManageModal;
