/**
 * Business Project Management Component
 *
 * Issue #871: Manage predefined business projects for workspace categorization
 */

import React, { useState, useEffect } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { useToast, Modal, Loading } from '@/components/common';
import {
  listBusinessProjects,
  createBusinessProject,
  updateBusinessProject,
  deleteBusinessProject,
  getBusinessProjectMembers,
  addBusinessProjectMember,
  removeBusinessProjectMember,
  getBusinessProjectStats,
} from '@/api/businessProjects';
import type {
  BusinessProject,
  BusinessProjectMember,
  BusinessProjectStats,
  CreateBusinessProjectRequest,
} from '@/api/businessProjects';
import { cn } from '@/utils';

interface BusinessProjectFormData {
  name: string;
  code: string;
  description: string;
  key_patterns: string[];
}

const defaultFormData: BusinessProjectFormData = {
  name: '',
  code: '',
  description: '',
  key_patterns: [],
};

export const BusinessProjectManagement: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();

  const [projects, setProjects] = useState<BusinessProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedProject, setSelectedProject] = useState<BusinessProject | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [showMembersModal, setShowMembersModal] = useState(false);
  const [formData, setFormData] = useState<BusinessProjectFormData>(defaultFormData);
  const [patternInput, setPatternInput] = useState('');
  const [members, setMembers] = useState<BusinessProjectMember[]>([]);
  const [stats, setStats] = useState<BusinessProjectStats | null>(null);
  const [memberUserIdInput, setMemberUserIdInput] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadProjects();
  }, []);

  const loadProjects = async () => {
    setLoading(true);
    try {
      const result = await listBusinessProjects(false, true);
      setProjects(result.projects || []);
    } catch (error) {
      console.error('Failed to load business projects:', error);
      toast.error(t('failedToLoadData', language));
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!formData.name.trim() || !formData.code.trim()) {
      toast.error(t('nameAndCodeRequired', language));
      return;
    }

    setSaving(true);
    try {
      const request: CreateBusinessProjectRequest = {
        name: formData.name.trim(),
        code: formData.code.trim(),
        description: formData.description.trim() || undefined,
        key_patterns: formData.key_patterns,
      };
      await createBusinessProject(request);
      toast.success(t('businessProjectCreated', language));
      setShowCreateModal(false);
      setFormData(defaultFormData);
      loadProjects();
    } catch (error: unknown) {
      console.error('Failed to create business project:', error);
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error ?? t('failedToSave', language));
    } finally {
      setSaving(false);
    }
  };

  const handleEdit = async () => {
    if (!selectedProject) return;
    if (!formData.name.trim() || !formData.code.trim()) {
      toast.error(t('nameAndCodeRequired', language));
      return;
    }

    setSaving(true);
    try {
      await updateBusinessProject(selectedProject.id, {
        name: formData.name.trim(),
        code: formData.code.trim(),
        description: formData.description.trim() || undefined,
        key_patterns: formData.key_patterns,
      });
      toast.success(t('businessProjectUpdated', language));
      setShowEditModal(false);
      setFormData(defaultFormData);
      setSelectedProject(null);
      loadProjects();
    } catch (error: unknown) {
      console.error('Failed to update business project:', error);
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error ?? t('failedToSave', language));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!selectedProject) return;

    setSaving(true);
    try {
      await deleteBusinessProject(selectedProject.id);
      toast.success(t('businessProjectDeleted', language));
      setShowDeleteModal(false);
      setSelectedProject(null);
      loadProjects();
    } catch (error) {
      console.error('Failed to delete business project:', error);
      toast.error(t('failedToDelete', language));
    } finally {
      setSaving(false);
    }
  };

  const handleAddPattern = () => {
    if (patternInput.trim()) {
      setFormData((prev) => ({
        ...prev,
        key_patterns: [...prev.key_patterns, patternInput.trim()],
      }));
      setPatternInput('');
    }
  };

  const handleRemovePattern = (index: number) => {
    setFormData((prev) => ({
      ...prev,
      key_patterns: prev.key_patterns.filter((_, i) => i !== index),
    }));
  };

  const openEditModal = (project: BusinessProject) => {
    setSelectedProject(project);
    setFormData({
      name: project.name,
      code: project.code,
      description: project.description ?? '',
      key_patterns: project.key_patterns ?? [],
    });
    setShowEditModal(true);
  };

  const openDeleteModal = (project: BusinessProject) => {
    setSelectedProject(project);
    setShowDeleteModal(true);
  };

  const openMembersModal = async (project: BusinessProject) => {
    setSelectedProject(project);
    setShowMembersModal(true);
    try {
      const result = await getBusinessProjectMembers(project.id);
      setMembers(result.members || []);
      const statsResult = await getBusinessProjectStats(project.id);
      setStats(statsResult.stats);
    } catch (error) {
      console.error('Failed to load members/stats:', error);
    }
  };

  const handleAddMember = async () => {
    if (!selectedProject || !memberUserIdInput.trim()) return;

    try {
      await addBusinessProjectMember(selectedProject.id, parseInt(memberUserIdInput.trim(), 10));
      toast.success(t('memberAdded', language));
      setMemberUserIdInput('');
      const result = await getBusinessProjectMembers(selectedProject.id);
      setMembers(result.members || []);
    } catch (error) {
      console.error('Failed to add member:', error);
      toast.error(t('failedToAddMember', language));
    }
  };

  const handleRemoveMember = async (memberId: number) => {
    if (!selectedProject) return;

    try {
      await removeBusinessProjectMember(selectedProject.id, memberId);
      toast.success(t('memberRemoved', language));
      const result = await getBusinessProjectMembers(selectedProject.id);
      setMembers(result.members || []);
    } catch (error) {
      console.error('Failed to remove member:', error);
      toast.error(t('failedToRemoveMember', language));
    }
  };

  const formatDuration = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  };

  const formatTokens = (tokens: number) => {
    if (tokens >= 1000000) {
      return `${(tokens / 1000000).toFixed(2)}M`;
    }
    if (tokens >= 1000) {
      return `${(tokens / 1000).toFixed(2)}K`;
    }
    return tokens.toString();
  };

  if (loading) {
    return (
      <div className="management-page">
        <Loading text={t('loading', language)} />
      </div>
    );
  }

  return (
    <div className="management-page">
      <div className="page-header">
        <h1>{t('businessProjects', language)}</h1>
        <p className="page-description">{t('businessProjectsDesc', language)}</p>
        <button className="btn btn-primary" onClick={() => setShowCreateModal(true)}>
          <i className="bi bi-plus-circle" />
          {t('createBusinessProject', language)}
        </button>
      </div>

      <div className="stats-summary">
        <div className="stat-card">
          <div className="stat-value">{projects.length}</div>
          <div className="stat-label">{t('totalBusinessProjects', language)}</div>
        </div>
      </div>

      {projects.length === 0 ? (
        <div className="empty-state">
          <i className="bi bi-briefcase empty-icon" />
          <h3>{t('noBusinessProjects', language)}</h3>
          <p>{t('noBusinessProjectsDesc', language)}</p>
        </div>
      ) : (
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('name', language)}</th>
                <th>{t('projectCode', language)}</th>
                <th>{t('keyPatterns', language)}</th>
                <th>{t('status', language)}</th>
                <th>{t('createdBy', language)}</th>
                <th>{t('actions', language)}</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((project) => (
                <tr key={project.id}>
                  <td>
                    <span className="project-name">{project.name}</span>
                    {project.description && (
                      <span className="project-description">{project.description}</span>
                    )}
                  </td>
                  <td>
                    <code className="project-code">{project.code}</code>
                  </td>
                  <td>
                    <div className="patterns-list">
                      {(project.key_patterns || []).length === 0 ? (
                        <span className="no-patterns">-</span>
                      ) : (
                        project.key_patterns.map((pattern, idx) => (
                          <span key={idx} className="pattern-tag">
                            {pattern}
                          </span>
                        ))
                      )}
                    </div>
                  </td>
                  <td>
                    <span className={cn('status-badge', project.is_active ? 'active' : 'inactive')}>
                      {project.is_active ? t('active', language) : t('inactive', language)}
                    </span>
                  </td>
                  <td>{project.created_by_username ?? '-'}</td>
                  <td>
                    <div className="action-buttons">
                      <button
                        className="btn btn-sm btn-outline"
                        onClick={() => openMembersModal(project)}
                        title={t('projectMembers', language)}
                      >
                        <i className="bi bi-people" />
                      </button>
                      <button
                        className="btn btn-sm btn-outline"
                        onClick={() => openEditModal(project)}
                        title={t('editBusinessProject', language)}
                      >
                        <i className="bi bi-pencil" />
                      </button>
                      <button
                        className="btn btn-sm btn-outline btn-danger"
                        onClick={() => openDeleteModal(project)}
                        title={t('deleteBusinessProject', language)}
                      >
                        <i className="bi bi-trash" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Modal */}
      <Modal
        isOpen={showCreateModal}
        onClose={() => {
          setShowCreateModal(false);
          setFormData(defaultFormData);
        }}
        title={t('createBusinessProject', language)}
      >
        <div className="modal-form">
          <div className="form-group">
            <label>{t('name', language)} *</label>
            <input
              type="text"
              className="form-control"
              value={formData.name}
              onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
              placeholder={t('namePlaceholder', language)}
            />
          </div>
          <div className="form-group">
            <label>{t('projectCode', language)} *</label>
            <input
              type="text"
              className="form-control"
              value={formData.code}
              onChange={(e) => setFormData((prev) => ({ ...prev, code: e.target.value }))}
              placeholder={t('codePlaceholder', language)}
            />
          </div>
          <div className="form-group">
            <label>{t('description', language)}</label>
            <textarea
              className="form-control"
              value={formData.description}
              onChange={(e) => setFormData((prev) => ({ ...prev, description: e.target.value }))}
              placeholder={t('descriptionPlaceholder', language)}
              rows={3}
            />
          </div>
          <div className="form-group">
            <label>{t('keyPatterns', language)}</label>
            <p className="form-help">{t('keyPatternsDesc', language)}</p>
            <div className="pattern-input-group">
              <input
                type="text"
                className="form-control"
                value={patternInput}
                onChange={(e) => setPatternInput(e.target.value)}
                placeholder={t('patternPlaceholder', language)}
              />
              <button className="btn btn-outline" onClick={handleAddPattern}>
                <i className="bi bi-plus" />
              </button>
            </div>
            <div className="patterns-list">
              {formData.key_patterns.map((pattern, idx) => (
                <span key={idx} className="pattern-tag">
                  {pattern}
                  <button
                    className="pattern-remove"
                    onClick={() => handleRemovePattern(idx)}
                    type="button"
                  >
                    <i className="bi bi-x" />
                  </button>
                </span>
              ))}
            </div>
          </div>
          <div className="modal-actions">
            <button
              className="btn btn-outline"
              onClick={() => {
                setShowCreateModal(false);
                setFormData(defaultFormData);
              }}
            >
              {t('cancel', language)}
            </button>
            <button className="btn btn-primary" onClick={handleCreate} disabled={saving}>
              {saving ? <i className="bi bi-spinner bi-spin" /> : null}
              {t('create', language)}
            </button>
          </div>
        </div>
      </Modal>

      {/* Edit Modal */}
      <Modal
        isOpen={showEditModal}
        onClose={() => {
          setShowEditModal(false);
          setFormData(defaultFormData);
          setSelectedProject(null);
        }}
        title={t('editBusinessProject', language)}
      >
        <div className="modal-form">
          <div className="form-group">
            <label>{t('name', language)} *</label>
            <input
              type="text"
              className="form-control"
              value={formData.name}
              onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
            />
          </div>
          <div className="form-group">
            <label>{t('projectCode', language)} *</label>
            <input
              type="text"
              className="form-control"
              value={formData.code}
              onChange={(e) => setFormData((prev) => ({ ...prev, code: e.target.value }))}
            />
          </div>
          <div className="form-group">
            <label>{t('description', language)}</label>
            <textarea
              className="form-control"
              value={formData.description}
              onChange={(e) => setFormData((prev) => ({ ...prev, description: e.target.value }))}
              rows={3}
            />
          </div>
          <div className="form-group">
            <label>{t('keyPatterns', language)}</label>
            <p className="form-help">{t('keyPatternsDesc', language)}</p>
            <div className="pattern-input-group">
              <input
                type="text"
                className="form-control"
                value={patternInput}
                onChange={(e) => setPatternInput(e.target.value)}
                placeholder={t('patternPlaceholder', language)}
              />
              <button className="btn btn-outline" onClick={handleAddPattern}>
                <i className="bi bi-plus" />
              </button>
            </div>
            <div className="patterns-list">
              {formData.key_patterns.map((pattern, idx) => (
                <span key={idx} className="pattern-tag">
                  {pattern}
                  <button
                    className="pattern-remove"
                    onClick={() => handleRemovePattern(idx)}
                    type="button"
                  >
                    <i className="bi bi-x" />
                  </button>
                </span>
              ))}
            </div>
          </div>
          <div className="modal-actions">
            <button
              className="btn btn-outline"
              onClick={() => {
                setShowEditModal(false);
                setFormData(defaultFormData);
                setSelectedProject(null);
              }}
            >
              {t('cancel', language)}
            </button>
            <button className="btn btn-primary" onClick={handleEdit} disabled={saving}>
              {saving ? <i className="bi bi-spinner bi-spin" /> : null}
              {t('save', language)}
            </button>
          </div>
        </div>
      </Modal>

      {/* Delete Modal */}
      <Modal
        isOpen={showDeleteModal}
        onClose={() => {
          setShowDeleteModal(false);
          setSelectedProject(null);
        }}
        title={t('deleteBusinessProject', language)}
      >
        <div className="modal-content">
          <p>
            {t('confirmDeleteBusinessProject', language)} <strong>{selectedProject?.name}</strong>?
          </p>
          <p className="warning-text">{t('deleteBusinessProjectWarning', language)}</p>
          <div className="modal-actions">
            <button
              className="btn btn-outline"
              onClick={() => {
                setShowDeleteModal(false);
                setSelectedProject(null);
              }}
            >
              {t('cancel', language)}
            </button>
            <button className="btn btn-danger" onClick={handleDelete} disabled={saving}>
              {saving ? <i className="bi bi-spinner bi-spin" /> : null}
              {t('delete', language)}
            </button>
          </div>
        </div>
      </Modal>

      {/* Members Modal */}
      <Modal
        isOpen={showMembersModal}
        onClose={() => {
          setShowMembersModal(false);
          setSelectedProject(null);
          setMembers([]);
          setStats(null);
        }}
        title={`${t('projectMembers', language)} - ${selectedProject?.name}`}
      >
        <div className="modal-content">
          {stats && (
            <div className="stats-section">
              <h4>{t('statistics', language)}</h4>
              <div className="stats-grid">
                <div className="stat-item">
                  <span className="stat-label">{t('totalWorkspaces', language)}</span>
                  <span className="stat-value">{stats.total_workspaces}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">{t('totalTokens', language)}</span>
                  <span className="stat-value">{formatTokens(stats.total_tokens)}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">{t('totalRequests', language)}</span>
                  <span className="stat-value">{stats.total_requests}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">{t('totalDuration', language)}</span>
                  <span className="stat-value">{formatDuration(stats.total_duration_seconds)}</span>
                </div>
              </div>
            </div>
          )}

          <div className="members-section">
            <h4>{t('members', language)}</h4>
            <div className="member-add-group">
              <input
                type="text"
                className="form-control"
                value={memberUserIdInput}
                onChange={(e) => setMemberUserIdInput(e.target.value)}
                placeholder={t('userIdPlaceholder', language)}
              />
              <button className="btn btn-outline" onClick={handleAddMember}>
                <i className="bi bi-plus" />
                {t('addMember', language)}
              </button>
            </div>
            {members.length === 0 ? (
              <div className="empty-state-small">
                <p>{t('noMembers', language)}</p>
                <p className="help-text">{t('noMembersDesc', language)}</p>
              </div>
            ) : (
              <ul className="members-list">
                {members.map((member) => (
                  <li key={member.id} className="member-item">
                    <span className="member-name">{member.username}</span>
                    <span className="member-id">(ID: {member.user_id})</span>
                    <button
                      className="btn btn-sm btn-outline btn-danger"
                      onClick={() => handleRemoveMember(member.id)}
                      title={t('remove', language)}
                    >
                      <i className="bi bi-x" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="modal-actions">
            <button
              className="btn btn-outline"
              onClick={() => {
                setShowMembersModal(false);
                setSelectedProject(null);
                setMembers([]);
                setStats(null);
              }}
            >
              {t('close', language)}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
};
