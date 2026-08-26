/**
 * ProjectEditModal Component - Edit project information modal
 *
 * Issue #3064: Project management page lacks project edit functionality.
 * Backend API (PUT /api/projects/<project_id>) already exists.
 * This modal provides the frontend UI to edit project name, description, and shared status.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Modal, Button, TextInput, Textarea, Switch, Loading } from '@/components/common';
import { useToast } from '@/components/common';
import { updateProject, getProject, type ProjectStats } from '@/api/projects';

// Matches backend validate_project_name in app/utils/validators.py
// Allowed: letters, digits, underscore, hyphen, space, Chinese characters
const PROJECT_NAME_FORBIDDEN_PATTERN = /[\t\n\r\f\v<>/\\]/;

interface ProjectEditModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  project: ProjectStats | null;
}

interface FormData {
  name: string;
  description: string;
  is_shared: boolean;
}

interface FormErrors {
  name?: string;
  description?: string;
}

/**
 * Modal for editing a project's name, description, and shared status.
 *
 * Validation rules (matching backend validate_project_name):
 * - name: max 255 chars, no forbidden characters (tabs, newlines, path separators, HTML/XML special chars)
 * - description: max 1000 chars (frontend limit; backend has no explicit limit)
 *
 * Permission control: The edit button is shown to all users; backend enforces
 * creator/admin-only access. If a non-authorized user attempts to edit,
 * the backend returns 403 and the frontend displays an error message.
 */
export const ProjectEditModal: React.FC<ProjectEditModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
  project,
}) => {
  const language = useLanguage();
  const toast = useToast();

  const [formData, setFormData] = useState<FormData>({
    name: '',
    description: '',
    is_shared: false,
  });
  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isLoadingProject, setIsLoadingProject] = useState(false);

  // Fetch full project details (description, is_shared) when modal opens
  // ProjectStats doesn't include these fields, so we call getProject API
  useEffect(() => {
    if (!project || !isOpen) return;

    let cancelled = false;
    const fetchProjectDetails = async () => {
      setIsLoadingProject(true);
      try {
        const response = await getProject(project.project_id);
        if (cancelled) return;
        const projectData = response.project;
        setFormData({
          name: projectData.name ?? project.project_name ?? '',
          description: projectData.description ?? '',
          is_shared: projectData.is_shared ?? false,
        });
        setErrors({});
      } catch {
        if (cancelled) return;
        // Fallback to ProjectStats data if fetch fails
        setFormData({
          name: project.project_name ?? '',
          description: '',
          is_shared: false,
        });
        setErrors({});
      } finally {
        if (!cancelled) setIsLoadingProject(false);
      }
    };

    fetchProjectDetails();

    return () => {
      cancelled = true;
    };
  }, [project, isOpen]);

  // Validate form
  const validate = useCallback((): boolean => {
    const newErrors: FormErrors = {};

    // Name validation (matching backend validate_project_name)
    const trimmedName = formData.name.trim();
    if (!trimmedName) {
      newErrors.name = t('projectNameRequired', language);
    } else {
      if (trimmedName.length > 255) {
        newErrors.name = t('projectNameMaxLength', language);
      }
      if (PROJECT_NAME_FORBIDDEN_PATTERN.test(trimmedName)) {
        newErrors.name = t('projectNameInvalidChars', language);
      }
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

    if (!project) return;

    if (!validate()) {
      return;
    }

    setIsSubmitting(true);
    try {
      await updateProject(project.project_id, {
        name: formData.name.trim(),
        description: formData.description.trim(),
        is_shared: formData.is_shared,
      });
      toast.success(t('projectUpdated', language));
      onSuccess();
      onClose();
    } catch (err: unknown) {
      const error = err as { message?: string; status?: number };
      const status = error?.status;
      let errorMessage = error?.message ?? 'Failed to update project';

      // Handle specific error codes
      if (status === 403) {
        errorMessage = t('projectEditNoPermission', language);
      } else if (status === 404) {
        errorMessage = t('projectEditNotFound', language);
      } else if (status === 503) {
        errorMessage = t('projectShareAsyncProcessing', language);
      }

      toast.error(errorMessage);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!project) return null;

  const projectDisplayName =
    project.project_name ?? project.project_path.split(/[/\\]/).pop() ?? 'Project';

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`${t('editProject', language)}: ${projectDisplayName}`}
      size="md"
    >
      <form onSubmit={handleSubmit}>
        {isLoadingProject && <Loading size="sm" text={t('loading', language)} />}

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
          required
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

        {/* Footer */}
        <div className="modal-footer border-0 px-0 pb-0">
          <Button variant="secondary" onClick={onClose} disabled={isSubmitting || isLoadingProject}>
            {t('cancel', language)}
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={isSubmitting}
            disabled={isLoadingProject}
          >
            {t('save', language)}
          </Button>
        </div>
      </form>
    </Modal>
  );
};

export default ProjectEditModal;
