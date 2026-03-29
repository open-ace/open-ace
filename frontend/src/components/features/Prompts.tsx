/**
 * Prompts Component - Prompt template management with CRUD operations
 */

import React, { useState, useEffect, useMemo } from 'react';
import { cn } from '@/utils';
import { promptsApi } from '@/api';
import type { PromptTemplate, PromptVariable, CategoryInfo } from '@/api';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  Badge,
  Modal,
  TextInput,
  Textarea,
} from '@/components/common';
import type { BadgeVariant } from '@/components/common';

const ITEMS_PER_PAGE = 20;

// Category colors
const categoryColors: Record<string, BadgeVariant> = {
  general: 'secondary',
  coding: 'primary',
  writing: 'success',
  analysis: 'info',
  translation: 'warning',
  summarization: 'dark',
  custom: 'light',
};

export const Prompts: React.FC = () => {
  const language = useLanguage() as Language;
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [categories, setCategories] = useState<CategoryInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [isFetching, setIsFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<{ category?: string; search?: string }>({});
  const [selectedTemplate, setSelectedTemplate] = useState<PromptTemplate | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showRenderModal, setShowRenderModal] = useState(false);
  const [renderResult, setRenderResult] = useState<string | null>(null);

  // Load categories on mount
  useEffect(() => {
    const loadCategories = async () => {
      try {
        const cats = await promptsApi.getCategories();
        setCategories(cats);
      } catch (err) {
        console.error('Failed to load categories:', err);
      }
    };
    loadCategories();
  }, []);

  // Load templates
  const loadTemplates = async (resetPage = false) => {
    try {
      setIsFetching(true);
      setError(null);
      const currentPage = resetPage ? 1 : page;
      if (resetPage) setPage(1);

      const result = await promptsApi.list({
        ...filters,
        page: currentPage,
        limit: ITEMS_PER_PAGE,
      });

      setTemplates(result.templates);
      setTotal(result.total);
    } catch (err) {
      const error = err as Error;
      setError(error?.message || t('error', language));
    } finally {
      setIsLoading(false);
      setIsFetching(false);
    }
  };

  // Load on mount and when filters change
  useEffect(() => {
    loadTemplates(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.category, filters.search]);

  // Category options
  const categoryOptions = useMemo(
    () => [
      { value: '', label: t('allCategories', language) || 'All Categories' },
      ...categories.map((cat) => ({
        value: cat.category,
        label: `${cat.category} (${cat.count})`,
      })),
    ],
    [categories, language]
  );

  // Handlers
  const handleFilterChange = (key: string, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
    setSelectedTemplate(null);
  };

  const handleSearch = (value: string) => {
    setFilters((prev) => ({ ...prev, search: value || undefined }));
  };

  const handleReset = () => {
    setFilters({});
    setPage(1);
    setSelectedTemplate(null);
  };

  const handleDelete = async (id: number) => {
    if (
      !window.confirm(
        t('confirmDeletePrompt', language) || 'Are you sure you want to delete this prompt?'
      )
    ) {
      return;
    }
    try {
      await promptsApi.delete(id);
      loadTemplates();
      if (selectedTemplate?.id === id) {
        setSelectedTemplate(null);
      }
    } catch (err) {
      const error = err as Error;
      setError(error?.message || t('error', language));
    }
  };

  const handleCreateSuccess = () => {
    setShowCreateModal(false);
    loadTemplates();
  };

  const handleEditSuccess = () => {
    setShowEditModal(false);
    loadTemplates();
    if (selectedTemplate) {
      // Refresh selected template
      promptsApi.get(selectedTemplate.id).then(setSelectedTemplate);
    }
  };

  const handleRender = async (templateId: number, variables: Record<string, string>) => {
    try {
      const result = await promptsApi.render(templateId, variables);
      setRenderResult(result);
    } catch (err) {
      const error = err as Error;
      setError(error?.message || t('error', language));
    }
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error && templates.length === 0) {
    return <Error message={error} onRetry={() => loadTemplates()} />;
  }

  return (
    <div className="prompts">
      {/* Header */}
      <div className="prompts-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('prompts', language)}</h2>
        <div className="d-flex gap-2 align-items-center">
          <Button
            variant="primary"
            size="sm"
            onClick={() => setShowCreateModal(true)}
            icon={<i className="bi bi-plus-lg" />}
          >
            {t('addPrompt', language) || 'Add Prompt'}
          </Button>
          <Button
            variant="outline-secondary"
            size="sm"
            onClick={() => loadTemplates()}
            loading={isFetching}
            icon={isFetching ? undefined : <i className="bi bi-arrow-clockwise" />}
          >
            {t('refresh', language)}
          </Button>
        </div>
      </div>

      {/* Filters */}
      <Card className="mb-3">
        <div className="d-flex flex-nowrap align-items-center gap-2">
          {/* Search */}
          <input
            type="text"
            className="form-control"
            style={{ maxWidth: '300px', height: '31px' }}
            placeholder={t('searchPrompts', language) || 'Search prompts...'}
            value={filters.search ?? ''}
            onChange={(e) => handleSearch(e.target.value)}
          />
          {/* Category Filter */}
          <Select
            options={categoryOptions}
            value={filters.category ?? ''}
            onChange={(value) => handleFilterChange('category', value)}
            size="sm"
            style={{ width: 'auto', minWidth: '120px' }}
          />
          {/* Reset Button */}
          <Button variant="outline-primary" size="sm" onClick={handleReset} className="text-nowrap">
            <i className="bi bi-arrow-counterclockwise me-1" />
            {t('reset', language)}
          </Button>
        </div>
      </Card>

      {/* Templates Grid */}
      {templates.length === 0 ? (
        <EmptyState
          icon="bi-file-text"
          title={t('noPromptsFound', language) || 'No prompts found'}
          description={
            t('noPromptsFoundHelp', language) || 'Create your first prompt template to get started'
          }
        />
      ) : (
        <>
          <div className="row">
            {templates.map((template) => (
              <div key={template.id} className="col-md-6 col-lg-4 mb-3">
                <PromptCard
                  template={template}
                  language={language}
                  isSelected={selectedTemplate?.id === template.id}
                  onSelect={() =>
                    setSelectedTemplate(selectedTemplate?.id === template.id ? null : template)
                  }
                  onEdit={() => {
                    setSelectedTemplate(template);
                    setShowEditModal(true);
                  }}
                  onDelete={() => handleDelete(template.id)}
                  onRender={() => {
                    setSelectedTemplate(template);
                    setShowRenderModal(true);
                  }}
                />
              </div>
            ))}
          </div>

          {/* Pagination */}
          {total > ITEMS_PER_PAGE && (
            <div className="d-flex justify-content-center align-items-center gap-2 mt-3">
              <Button
                variant="outline-secondary"
                size="sm"
                disabled={page === 1}
                onClick={() => {
                  setPage(page - 1);
                  loadTemplates();
                }}
              >
                {t('previous', language)}
              </Button>
              <span className="text-muted">
                {t('page', language) || 'Page'} {page} / {Math.ceil(total / ITEMS_PER_PAGE)}
              </span>
              <Button
                variant="outline-secondary"
                size="sm"
                disabled={page >= Math.ceil(total / ITEMS_PER_PAGE)}
                onClick={() => {
                  setPage(page + 1);
                  loadTemplates();
                }}
              >
                {t('next', language)}
              </Button>
            </div>
          )}

          {/* Total count */}
          <div className="text-center text-muted mt-3">
            {t('total', language)}: {total} {t('prompts', language)}
          </div>
        </>
      )}

      {/* Create Modal */}
      <PromptFormModal
        language={language}
        isOpen={showCreateModal}
        onSuccess={handleCreateSuccess}
        onClose={() => setShowCreateModal(false)}
      />

      {/* Edit Modal */}
      {selectedTemplate && (
        <PromptFormModal
          language={language}
          isOpen={showEditModal}
          template={selectedTemplate}
          onSuccess={handleEditSuccess}
          onClose={() => setShowEditModal(false)}
        />
      )}

      {/* Render Modal */}
      {selectedTemplate && (
        <RenderModal
          template={selectedTemplate}
          language={language}
          isOpen={showRenderModal}
          result={renderResult}
          onRender={(vars) => handleRender(selectedTemplate.id, vars)}
          onClose={() => {
            setShowRenderModal(false);
            setRenderResult(null);
          }}
        />
      )}
    </div>
  );
};

/**
 * Prompt Card Component
 */
interface PromptCardProps {
  template: PromptTemplate;
  language: Language;
  isSelected: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onRender: () => void;
}

const PromptCard: React.FC<PromptCardProps> = ({
  template,
  language,
  isSelected,
  onSelect,
  onEdit,
  onDelete,
  onRender,
}) => {
  return (
    <div className={cn('card h-100', isSelected && 'border-primary')}>
      <div className="card-body d-flex flex-column">
        {/* Header */}
        <div className="d-flex justify-content-between align-items-start mb-2">
          <h5 className="card-title mb-0" style={{ cursor: 'pointer' }} onClick={onSelect}>
            {template.name}
          </h5>
          <div className="d-flex gap-1">
            {template.is_featured && (
              <Badge variant="warning">
                <i className="bi bi-star-fill" />
              </Badge>
            )}
            {template.is_public && (
              <Badge variant="info">
                <i className="bi bi-globe" />
              </Badge>
            )}
          </div>
        </div>

        {/* Category & Tags */}
        <div className="mb-2">
          <Badge variant={categoryColors[template.category] || 'secondary'}>
            {template.category}
          </Badge>
          {template.tags.slice(0, 3).map((tag) => (
            <Badge key={tag} variant="light" className="ms-1">
              {tag}
            </Badge>
          ))}
        </div>

        {/* Description */}
        <p className="card-text text-muted small flex-grow-1">
          {template.description || template.content.substring(0, 100) + '...'}
        </p>

        {/* Meta */}
        <div className="d-flex justify-content-between align-items-center text-muted small mb-2">
          <span>
            <i className="bi bi-person me-1" />
            {template.author_name || t('anonymous', language) || 'Anonymous'}
          </span>
          <span>
            <i className="bi bi-play-circle me-1" />
            {template.use_count} {t('uses', language) || 'uses'}
          </span>
        </div>

        {/* Actions */}
        <div className="d-flex gap-1 mt-auto">
          <Button variant="outline-primary" size="sm" onClick={onRender}>
            <i className="bi bi-play-fill me-1" />
            {t('render', language) || 'Render'}
          </Button>
          <Button variant="outline-secondary" size="sm" onClick={onEdit}>
            <i className="bi bi-pencil" />
          </Button>
          <Button variant="outline-danger" size="sm" onClick={onDelete}>
            <i className="bi bi-trash" />
          </Button>
        </div>
      </div>
    </div>
  );
};

/**
 * Prompt Form Modal Component
 */
interface PromptFormModalProps {
  language: Language;
  template?: PromptTemplate;
  isOpen: boolean;
  onSuccess: () => void;
  onClose: () => void;
}

const PromptFormModal: React.FC<PromptFormModalProps> = ({
  language,
  template,
  isOpen,
  onSuccess,
  onClose,
}) => {
  const [name, setName] = useState(template?.name ?? '');
  const [description, setDescription] = useState(template?.description ?? '');
  const [category, setCategory] = useState(template?.category ?? 'general');
  const [content, setContent] = useState(template?.content ?? '');
  const [tags, setTags] = useState(template?.tags.join(', ') ?? '');
  const [isPublic, setIsPublic] = useState(template?.is_public ?? false);
  const [variables, setVariables] = useState<PromptVariable[]>(template?.variables ?? []);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isEdit = !!template;

  // Reset form when modal opens
  useEffect(() => {
    if (isOpen) {
      setName(template?.name ?? '');
      setDescription(template?.description ?? '');
      setCategory(template?.category ?? 'general');
      setContent(template?.content ?? '');
      setTags(template?.tags.join(', ') ?? '');
      setIsPublic(template?.is_public ?? false);
      setVariables(template?.variables ?? []);
      setError(null);
    }
  }, [isOpen, template]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name || !content) {
      setError(t('requiredFields', language) || 'Name and content are required');
      return;
    }

    try {
      setIsLoading(true);
      setError(null);

      const data = {
        name,
        description,
        category,
        content,
        tags: tags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        is_public: isPublic,
        variables,
      };

      if (isEdit && template) {
        await promptsApi.update(template.id, data);
      } else {
        await promptsApi.create(data);
      }

      onSuccess();
    } catch (err) {
      const error = err as Error;
      setError(error?.message || t('error', language));
    } finally {
      setIsLoading(false);
    }
  };

  const addVariable = () => {
    setVariables([...variables, { name: '', description: '', required: false, default: '' }]);
  };

  const updateVariable = (index: number, field: keyof PromptVariable, value: string | boolean) => {
    const updated = [...variables];
    updated[index] = { ...updated[index], [field]: value };
    setVariables(updated);
  };

  const removeVariable = (index: number) => {
    setVariables(variables.filter((_, i) => i !== index));
  };

  const categoryOptions = [
    { value: 'general', label: 'General' },
    { value: 'coding', label: 'Coding' },
    { value: 'writing', label: 'Writing' },
    { value: 'analysis', label: 'Analysis' },
    { value: 'translation', label: 'Translation' },
    { value: 'summarization', label: 'Summarization' },
    { value: 'custom', label: 'Custom' },
  ];

  return (
    <Modal
      isOpen={isOpen}
      title={
        isEdit
          ? t('editPrompt', language) || 'Edit Prompt'
          : t('addPrompt', language) || 'Add Prompt'
      }
      onClose={onClose}
      size="lg"
    >
      <form onSubmit={handleSubmit}>
        {error && <div className="alert alert-danger">{error}</div>}

        <div className="mb-3">
          <TextInput
            label={t('promptName', language) || 'Name'}
            value={name}
            onChange={setName}
            placeholder={t('enterPromptName', language) || 'Enter prompt name'}
            required
          />
        </div>

        <div className="mb-3">
          <TextInput
            label={t('description', language)}
            value={description}
            onChange={setDescription}
            placeholder={t('enterDescription', language) || 'Enter description'}
          />
        </div>

        <div className="mb-3">
          <label className="form-label">{t('category', language) || 'Category'}</label>
          <Select options={categoryOptions} value={category} onChange={setCategory} />
        </div>

        <div className="mb-3">
          <Textarea
            label={t('promptContent', language) || 'Content'}
            value={content}
            onChange={setContent}
            placeholder={
              t('promptContentHelp', language) ||
              'Enter prompt content. Use {variable_name} for variables.'
            }
            rows={6}
            required
          />
          <small className="text-muted">
            {t('promptContentHelp', language) || 'Use {variable_name} for variables.'}
          </small>
        </div>

        <div className="mb-3">
          <TextInput
            label={t('tags', language) || 'Tags'}
            value={tags}
            onChange={setTags}
            placeholder={t('tagsHelp', language) || 'Enter tags separated by commas'}
          />
        </div>

        {/* Variables */}
        <div className="mb-3">
          <div className="d-flex justify-content-between align-items-center mb-2">
            <label className="form-label mb-0">{t('variables', language) || 'Variables'}</label>
            <Button type="button" variant="outline-secondary" size="sm" onClick={addVariable}>
              <i className="bi bi-plus" /> {t('addVariable', language) || 'Add Variable'}
            </Button>
          </div>
          {variables.length > 0 && (
            <div className="border rounded p-2">
              {variables.map((variable, index) => (
                <div key={index} className="row g-2 mb-2">
                  <div className="col-md-3">
                    <TextInput
                      value={variable.name}
                      onChange={(value) => updateVariable(index, 'name', value)}
                      placeholder={t('variableName', language) || 'Name'}
                    />
                  </div>
                  <div className="col-md-4">
                    <TextInput
                      value={variable.description ?? ''}
                      onChange={(value) => updateVariable(index, 'description', value)}
                      placeholder={t('description', language)}
                    />
                  </div>
                  <div className="col-md-3">
                    <TextInput
                      value={variable.default ?? ''}
                      onChange={(value) => updateVariable(index, 'default', value)}
                      placeholder={t('defaultValue', language) || 'Default'}
                    />
                  </div>
                  <div className="col-md-2 d-flex align-items-center">
                    <div className="form-check me-2">
                      <input
                        type="checkbox"
                        className="form-check-input"
                        id={`required-${index}`}
                        checked={variable.required}
                        onChange={(e) => updateVariable(index, 'required', e.target.checked)}
                      />
                      <label className="form-check-label small" htmlFor={`required-${index}`}>
                        {t('required', language) || 'Req'}
                      </label>
                    </div>
                    <Button
                      type="button"
                      variant="outline-danger"
                      size="sm"
                      onClick={() => removeVariable(index)}
                    >
                      <i className="bi bi-x" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="mb-3">
          <div className="form-check">
            <input
              type="checkbox"
              className="form-check-input"
              id="isPublic"
              checked={isPublic}
              onChange={(e) => setIsPublic(e.target.checked)}
            />
            <label className="form-check-label" htmlFor="isPublic">
              {t('isPublic', language) || 'Make this prompt public'}
            </label>
          </div>
        </div>

        <div className="d-flex justify-content-end gap-2">
          <Button type="button" variant="outline-secondary" onClick={onClose}>
            {t('cancel', language)}
          </Button>
          <Button type="submit" variant="primary" loading={isLoading}>
            {isEdit ? t('save', language) : t('create', language) || 'Create'}
          </Button>
        </div>
      </form>
    </Modal>
  );
};

/**
 * Render Modal Component
 */
interface RenderModalProps {
  template: PromptTemplate;
  language: Language;
  isOpen: boolean;
  result: string | null;
  onRender: (variables: Record<string, string>) => void;
  onClose: () => void;
}

const RenderModal: React.FC<RenderModalProps> = ({
  template,
  language,
  isOpen,
  result,
  onRender,
  onClose,
}) => {
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(false);

  // Reset variables when modal opens
  useEffect(() => {
    if (isOpen) {
      const initialVars: Record<string, string> = {};
      template.variables.forEach((v) => {
        initialVars[v.name] = v.default ?? '';
      });
      setVariables(initialVars);
    }
  }, [isOpen, template.variables]);

  const handleRender = async () => {
    setIsLoading(true);
    try {
      onRender(variables);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCopy = () => {
    if (result) {
      navigator.clipboard.writeText(result);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      title={t('renderPrompt', language) || 'Render Prompt'}
      onClose={onClose}
      size="lg"
    >
      <div className="mb-3">
        <h5>{template.name}</h5>
        <p className="text-muted small">{template.description}</p>
      </div>

      {/* Variables Input */}
      {template.variables.length > 0 && (
        <div className="mb-3">
          <label className="form-label">{t('variables', language) || 'Variables'}</label>
          {template.variables.map((variable) => (
            <div key={variable.name} className="mb-2">
              <label className="form-label small">
                {variable.name}
                {variable.required && <span className="text-danger">*</span>}
                {variable.description && (
                  <span className="text-muted ms-2">({variable.description})</span>
                )}
              </label>
              <TextInput
                value={variables[variable.name] ?? variable.default ?? ''}
                onChange={(value) => setVariables({ ...variables, [variable.name]: value })}
                placeholder={variable.description ?? variable.name}
              />
            </div>
          ))}
        </div>
      )}

      {/* Render Button */}
      <div className="mb-3">
        <Button variant="primary" onClick={handleRender} loading={isLoading}>
          <i className="bi bi-play-fill me-1" />
          {t('render', language) || 'Render'}
        </Button>
      </div>

      {/* Result */}
      {result && (
        <div className="mb-3">
          <div className="d-flex justify-content-between align-items-center mb-2">
            <label className="form-label mb-0">{t('result', language) || 'Result'}</label>
            <Button variant="outline-secondary" size="sm" onClick={handleCopy}>
              <i className="bi bi-clipboard me-1" />
              {t('copy', language) || 'Copy'}
            </Button>
          </div>
          <div className="border rounded p-3 bg-light" style={{ whiteSpace: 'pre-wrap' }}>
            {result}
          </div>
        </div>
      )}

      <div className="d-flex justify-content-end">
        <Button variant="outline-secondary" onClick={onClose}>
          {t('close', language)}
        </Button>
      </div>
    </Modal>
  );
};
