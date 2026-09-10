/**
 * ProjectCreateModal Component - Create new project modal
 *
 * Issue #3372: Project management page lacks create project functionality
 * Backend API (POST /api/projects) already exists.
 * This modal provides the frontend UI to create a new project with path, name, description, and shared status.
 */

import React, { useState, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Modal, Button, TextInput, Textarea, Switch } from '@/components/common';
import { useToast } from '@/components/common';
import { createProject } from '@/api/projects';

// Matches backend validate_project_name in app/utils/validators.py
// Allowed: letters, digits, underscore, hyphen, space, Chinese characters
const PROJECT_NAME_FORBIDDEN_PATTERN = /[\t\n\r\f\v<>/\\]/;

interface ProjectCreateModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

interface FormData {
  path: string;
  name: string;
  description: string;
  is_shared: boolean;
  create_dir: boolean;
}

interface FormErrors {
  path?: string;
  name?: string;
  description?: string;
}

/**
 * Modal for creating a new project.
 *
 * Fields:
 * - path (required): Absolute path for the project directory
 * - name (optional): Display name for the project
 * - description (optional): Project description
 * - is_shared (optional): Whether the project is shared with other users
 * - create_dir (optional): Whether to create the directory if it doesn't exist
 *
 * Validation rules:
 * - path: required, must be absolute (start with /)
 * - name: max 255 chars, no forbidden characters
 * - description: max 1000 chars
 */
export const ProjectCreateModal: React.FC<ProjectCreateModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
}) => {
  const language = useLanguage();
  const toast = useToast();

  const [formData, setFormData] = useState<FormData>({
    path: '',
    name: '',
    description: '',
    is_shared: false,
    create_dir: true,
  });
  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Reset form when modal opens
  const resetForm = useCallback(() => {
    setFormData({
      path: '',
      name: '',
      description: '',
      is_shared: false,
      create_dir: true,
    });
    setErrors({});
  }, []);

  // Validate form
  const validate = useCallback((): boolean => {
    const newErrors: FormErrors = {};

    // Path validation
    const trimmedPath = formData.path.trim();
    if (!trimmedPath) {
      newErrors.path = t('projectPathRequired', language);
    } else if (!trimmedPath.startsWith('/')) {
      newErrors.path = t('projectPathAbsolute', language);
    }

    // Name validation (optional but must be valid if provided)
    const trimmedName = formData.name.trim();
    if (trimmedName.length > 255) {
      newErrors.name = t('projectNameMaxLength', language);
    } else if (trimmedName.length > 0 && PROJECT_NAME_FORBIDDEN_PATTERN.test(trimmedName)) {
      newErrors.name = t('projectNameInvalidChars', language);
    }

    // Description validation
    if (formData.description.length > 1000) {
      newErrors.description = t('projectDescriptionMaxLength', language);
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
      const response = await createProject({
        path: formData.path.trim(),
        name: formData.name.trim() || undefined,
        description: formData.description.trim() || undefined,
        is_shared: formData.is_shared,
        create_dir: formData.create_dir,
      });
      toast.success(t('projectCreated', language));

      // Show permission warning if present (Docker multi-user mode)
      if (response.permission_warning) {
        toast.warning(response.permission_warning);
      }

      onSuccess();
      onClose();
      resetForm();
    } catch (err: unknown) {
      const error = err as { message?: string; status?: number };
      const status = error?.status;
      let errorMessage = error?.message ?? 'Failed to create project';

      // Handle specific error codes
      if (status === 409) {
        errorMessage = t('projectAlreadyExists', language);
      } else if (status === 403) {
        errorMessage = t('projectCreateNoPermission', language);
      } else if (status === 400) {
        // Use backend error message directly for validation errors
        errorMessage = error?.message ?? t('projectCreateFailed', language);
      }

      toast.error(errorMessage);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  if (!isOpen) return null;

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title={t('createProject', language)} size="md">
      <form onSubmit={handleSubmit} noValidate>
        {/* Project Path */}
        <TextInput
          label={t('projectPath', language)}
          value={formData.path}
          onChange={(value) => {
            setFormData((prev) => ({ ...prev, path: value }));
            if (errors.path) {
              setErrors((prev) => ({ ...prev, path: undefined }));
            }
          }}
          error={errors.path}
          required
          placeholder={t('projectPathPlaceholder', language)}
          hint={t('projectCreatePathHint', language)}
        />

        {/* Project Name */}
        <TextInput
          label={t('projectName', language)}
          value={formData.name}
          onChange={(value) => {
            const truncated = value.slice(0, 255);
            setFormData((prev) => ({ ...prev, name: truncated }));
            if (errors.name) {
              setErrors((prev) => ({ ...prev, name: undefined }));
            }
          }}
          error={errors.name}
          placeholder={t('projectNamePlaceholder', language)}
          hint={t('projectNameHint', language)}
        />

        {/* Project Description */}
        <Textarea
          label={t('projectDescription', language)}
          value={formData.description}
          onChange={(value) => {
            const truncated = value.slice(0, 1000);
            setFormData((prev) => ({ ...prev, description: truncated }));
            if (errors.description) {
              setErrors((prev) => ({ ...prev, description: undefined }));
            }
          }}
          error={errors.description}
          placeholder={t('projectDescriptionPlaceholder', language)}
          rows={3}
          maxLength={1000}
          showCount
        />

        {/* Shared Status */}
        <div className="mb-3">
          <Switch
            label={t('projectShared', language)}
            checked={formData.is_shared}
            onChange={(checked) => setFormData((prev) => ({ ...prev, is_shared: checked }))}
          />
          <small className="text-muted d-block mt-1">{t('projectSharedHint', language)}</small>
        </div>

        {/* Create Directory */}
        <div className="mb-3">
          <Switch
            label={t('projectCreateDir', language)}
            checked={formData.create_dir}
            onChange={(checked) => setFormData((prev) => ({ ...prev, create_dir: checked }))}
          />
          <small className="text-muted d-block mt-1">{t('projectCreateDirHint', language)}</small>
        </div>

        {/* Footer */}
        <div className="modal-footer border-0 px-0 pb-0">
          <Button variant="secondary" onClick={handleClose} disabled={isSubmitting}>
            {t('cancel', language)}
          </Button>
          <Button type="submit" variant="primary" loading={isSubmitting}>
            {t('createProject', language)}
          </Button>
        </div>
      </form>
    </Modal>
  );
};

export default ProjectCreateModal;
