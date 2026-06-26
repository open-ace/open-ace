/**
 * NewAutonomousModal Component - Modal for creating a new autonomous development workflow
 */

import React, { useState, useMemo, useCallback, useEffect } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Modal, Button } from '@/components/common';
import { RemoteMachineSelector } from './RemoteMachineSelector';
import { DirectoryBrowserModal } from './DirectoryBrowserModal';
import { LocalDirectoryBrowserModal } from './LocalDirectoryBrowserModal';
import { useCreateWorkflow, useAvailableTools, useAvailableModels } from '@/hooks/useAutonomous';
import type { AutonomousWorkflow, CreateWorkflowRequest } from '@/api/autonomous';

interface NewAutonomousModalProps {
  show: boolean;
  onClose: () => void;
  onCreated: (workflow: AutonomousWorkflow) => void;
}

const LOCAL_LAST_PROJECT_PATH_KEY = 'local-last-project-path';

function getRemoteLastProjectPathKey(machineId: string): string {
  return `remote-last-project-path-${machineId}`;
}

function getStoredPath(key: string): string {
  try {
    return localStorage.getItem(key) ?? '';
  } catch {
    return '';
  }
}

function setStoredPath(key: string, path: string): void {
  try {
    localStorage.setItem(key, path);
  } catch {
    // Ignore localStorage write failures
  }
}

// Regexes mirror the backend `_parse_issue_selectors` contract
// (ISSUE_URL_RE / ISSUE_RANGE_RE in app/routes/autonomous.py). Hoisted to
// module scope so they are allocated once, not per render.
const ISSUE_URL_RE = /^https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/issues\/\d+(?:[/?#].*)?$/i;
const ISSUE_RANGE_RE = /^(\d+)-(\d+)$/;

// Returns true only for tokens the backend would actually accept (rejecting
// non-positive numbers and inverted ranges), so the suggestion never nudges the
// user toward a mode where every token would be silently dropped.
function isAcceptableIssueToken(tok: string): boolean {
  if (/^\d+$/.test(tok)) return Number(tok) > 0;
  const rangeMatch = ISSUE_RANGE_RE.exec(tok);
  if (rangeMatch) {
    const start = Number(rangeMatch[1]);
    const end = Number(rangeMatch[2]);
    return start > 0 && end > 0 && start <= end;
  }
  return ISSUE_URL_RE.test(tok);
}

export const NewAutonomousModal: React.FC<NewAutonomousModalProps> = ({
  show,
  onClose,
  onCreated,
}) => {
  const language = useLanguage();

  // Form state
  const [requirementsMode, setRequirementsMode] = useState<'text' | 'url'>('text');
  const [requirementsText, setRequirementsText] = useState('');
  const [requirementsUrl, setRequirementsUrl] = useState('');
  const [cliTool, setCliTool] = useState('claude-code');
  const [model, setModel] = useState('');
  const [workspaceType, setWorkspaceType] = useState<'local' | 'remote'>('local');
  const [selectedMachineId, setSelectedMachineId] = useState('');
  const [selectedMachineOsType, setSelectedMachineOsType] = useState('');
  const [selectedMachineWorkDir, setSelectedMachineWorkDir] = useState('');
  const [projectPath, setProjectPath] = useState('');
  const [isNewProject, setIsNewProject] = useState(false);
  const [showLocalDirectoryBrowser, setShowLocalDirectoryBrowser] = useState(false);
  const [showRemoteDirectoryBrowser, setShowRemoteDirectoryBrowser] = useState(false);
  const [repoName, setRepoName] = useState('');
  const [isPrivate, setIsPrivate] = useState(true);
  const [branchStrategy, setBranchStrategy] = useState<'new-branch' | 'worktree' | 'current'>(
    'worktree'
  );
  const [branchName, setBranchName] = useState('');
  const [maxPlanRounds, setMaxPlanRounds] = useState(2);
  const [maxPRReviewRounds, setMaxPRReviewRounds] = useState(3);
  const [title, setTitle] = useState('');
  const [autoMerge, setAutoMerge] = useState(true); // Auto merge for batch workflows
  const [errorMessage, setErrorMessage] = useState('');

  // Data
  const { data: toolsData } = useAvailableTools();
  const { data: modelsData } = useAvailableModels(
    { tool: cliTool, workspace_type: workspaceType },
    !!cliTool
  );
  const createWorkflow = useCreateWorkflow();

  const tools = toolsData?.tools ?? [];
  const models = modelsData?.models ?? [];
  const isCreating = createWorkflow.isPending;

  const persistLastProjectPath = useCallback(
    (path: string, type: 'local' | 'remote', machineId?: string) => {
      const normalizedPath = path.trim();
      if (!normalizedPath) return;

      if (type === 'remote') {
        if (!machineId) return;
        setStoredPath(getRemoteLastProjectPathKey(machineId), normalizedPath);
        return;
      }

      setStoredPath(LOCAL_LAST_PROJECT_PATH_KEY, normalizedPath);
    },
    []
  );

  useEffect(() => {
    if (!show || isNewProject || workspaceType !== 'local' || projectPath) {
      return;
    }

    const storedPath = getStoredPath(LOCAL_LAST_PROJECT_PATH_KEY);
    if (storedPath) {
      setProjectPath(storedPath);
    }
  }, [show, isNewProject, workspaceType, projectPath]);

  useEffect(() => {
    if (!show || isNewProject || workspaceType !== 'remote' || !selectedMachineId) {
      return;
    }

    const storedPath = getStoredPath(getRemoteLastProjectPathKey(selectedMachineId));
    if (storedPath) {
      setProjectPath(storedPath);
      return;
    }

    if (selectedMachineWorkDir) {
      setProjectPath(selectedMachineWorkDir);
    }
  }, [show, isNewProject, workspaceType, selectedMachineId, selectedMachineWorkDir]);

  // Detect when a "Text Description" input actually looks like a GitHub issue
  // selector (bare numbers, ranges, or issue URLs). Mirrors the backend's
  // _parse_issue_selectors so we can nudge the user toward the correct mode
  // instead of silently storing e.g. "830" as a literal requirement body.
  const issueLikeSuggestion = useMemo(() => {
    if (requirementsMode !== 'text') return null;
    const trimmed = requirementsText.trim();
    if (!trimmed) return null;
    const tokens = trimmed.split(/[\s,]+/).filter(Boolean);
    if (tokens.length === 0) return null;
    // Only suggest when the ENTIRE input parses as issue selectors. A prose
    // description that merely contains a number ("see issue 830 for details")
    // must not trigger the nudge.
    return tokens.every(isAcceptableIssueToken) ? trimmed : null;
  }, [requirementsMode, requirementsText]);

  const switchToIssueMode = useCallback(() => {
    setRequirementsUrl(requirementsText);
    setRequirementsText('');
    setRequirementsMode('url');
  }, [requirementsText]);

  const canSubmit = useMemo(() => {
    const hasRequirements =
      requirementsMode === 'text' ? !!requirementsText.trim() : !!requirementsUrl.trim();
    const hasPath = isNewProject ? !!repoName.trim() : !!projectPath.trim();
    const hasRemote = workspaceType !== 'remote' || !!selectedMachineId;
    return hasRequirements && !!cliTool && hasPath && hasRemote;
  }, [
    requirementsMode,
    requirementsText,
    requirementsUrl,
    cliTool,
    projectPath,
    isNewProject,
    repoName,
    workspaceType,
    selectedMachineId,
  ]);

  const handleSubmit = useCallback(async () => {
    const confirmedProjectPath = isNewProject ? '' : projectPath.trim();
    const data: CreateWorkflowRequest = {
      title: title || undefined,
      requirements_text: requirementsMode === 'text' ? requirementsText : undefined,
      requirements_issue_input: requirementsMode === 'url' ? requirementsUrl : undefined,
      requirements_issue_url: undefined,
      cli_tool: cliTool,
      model: model || undefined,
      workspace_type: workspaceType,
      remote_machine_id: workspaceType === 'remote' ? selectedMachineId : undefined,
      project_path: isNewProject ? undefined : projectPath,
      is_new_project: isNewProject,
      project_repo_url: isNewProject ? repoName : undefined,
      is_private: isNewProject ? isPrivate : undefined,
      branch_strategy: branchStrategy,
      branch_name: branchName || undefined,
      max_plan_rounds: maxPlanRounds,
      max_pr_review_rounds: maxPRReviewRounds,
      auto_merge: autoMerge,
      // Persist the creator's current UI language as the workflow's content
      // language. AI-authored content is generated in this language.
      content_language: language,
    };

    try {
      setErrorMessage('');
      const result = await createWorkflow.mutateAsync(data);
      if (confirmedProjectPath) {
        persistLastProjectPath(confirmedProjectPath, workspaceType, selectedMachineId);
      }
      if (result.workflow) {
        onCreated(result.workflow);
      }
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'message' in err
          ? (err as { message: string }).message
          : t('autoCreateFailed', language) || 'Failed to create task';
      setErrorMessage(msg);
    }
  }, [
    title,
    requirementsMode,
    requirementsText,
    requirementsUrl,
    cliTool,
    model,
    workspaceType,
    selectedMachineId,
    projectPath,
    isNewProject,
    repoName,
    isPrivate,
    branchStrategy,
    branchName,
    maxPlanRounds,
    maxPRReviewRounds,
    autoMerge,
    createWorkflow,
    language,
    onCreated,
    persistLastProjectPath,
  ]);

  return (
    <>
      <Modal
        isOpen={show}
        onClose={onClose}
        title={t('newAutonomousTask', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={onClose}>
              {t('cancel', language)}
            </Button>
            <Button onClick={handleSubmit} disabled={!canSubmit || isCreating}>
              {isCreating ? (
                <>
                  <span className="spinner-border spinner-border-sm me-1" role="status"></span>
                  {t('autoCreating', language)}
                </>
              ) : (
                t('autoCreateTask', language)
              )}
            </Button>
          </>
        }
      >
        <div className="row g-3">
          {/* Error Alert */}
          {errorMessage && (
            <div className="col-12">
              <div className="alert alert-danger d-flex align-items-center" role="alert">
                <i className="bi bi-exclamation-triangle-fill me-2"></i>
                {errorMessage}
              </div>
            </div>
          )}

          {/* Title */}
          <div className="col-12">
            <label className="form-label fw-semibold">{t('autoTaskTitle', language)}</label>
            <input
              type="text"
              className="form-control"
              placeholder={t('autoTaskTitlePlaceholder', language)}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          {/* Requirements */}
          <div className="col-12">
            <label className="form-label fw-semibold">
              {t('autoRequirements', language)} <span className="text-danger">*</span>
            </label>
            <div className="btn-group btn-group-sm mb-2" role="group">
              <button
                type="button"
                className={`btn btn-outline-primary ${requirementsMode === 'text' ? 'active' : ''}`}
                onClick={() => setRequirementsMode('text')}
              >
                {t('autoTextDescription', language)}
              </button>
              <button
                type="button"
                className={`btn btn-outline-primary ${requirementsMode === 'url' ? 'active' : ''}`}
                onClick={() => setRequirementsMode('url')}
              >
                {t('autoGithubIssue', language)}
              </button>
            </div>
            {requirementsMode === 'text' ? (
              <>
                <textarea
                  className="form-control"
                  rows={4}
                  placeholder={t('autoRequirementsPlaceholder', language)}
                  value={requirementsText}
                  onChange={(e) => setRequirementsText(e.target.value)}
                />
                {issueLikeSuggestion && (
                  <div
                    className="alert alert-warning py-2 mt-2 d-flex align-items-center gap-2 flex-wrap"
                    role="alert"
                  >
                    <span>{t('autoIssueSuggestionHint', language)}</span>
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-primary ms-auto"
                      onClick={switchToIssueMode}
                    >
                      {t('autoIssueSuggestionSwitch', language)}
                    </button>
                  </div>
                )}
              </>
            ) : (
              <>
                <textarea
                  className="form-control"
                  rows={3}
                  placeholder={
                    t('autoIssueInputPlaceholder', language) ||
                    '123 125,128-130 or https://github.com/owner/repo/issues/123'
                  }
                  value={requirementsUrl}
                  onChange={(e) => setRequirementsUrl(e.target.value)}
                />
                <div className="form-text">
                  {t('autoIssueInputHint', language) ||
                    'Supports commas, spaces, new lines, ranges like 12-15, and mixed GitHub issue URLs.'}
                </div>
              </>
            )}
          </div>

          {/* Agent Tool & Model */}
          <div className="col-md-6">
            <label className="form-label fw-semibold">
              {t('autoAgentTool', language)} <span className="text-danger">*</span>
            </label>
            <select
              className="form-select"
              value={cliTool}
              onChange={(e) => {
                setCliTool(e.target.value);
                setModel('');
              }}
            >
              {tools.length > 0 ? (
                tools.map((tool) => (
                  <option key={tool.id} value={tool.id}>
                    {tool.name}
                  </option>
                ))
              ) : (
                <>
                  <option value="claude-code">Claude Code</option>
                  <option value="qwen-code-cli">Qwen Code</option>
                  <option value="codex">Codex CLI</option>
                  <option value="openclaw">OpenClaw</option>
                  <option value="zcode">ZCode</option>
                </>
              )}
            </select>
          </div>
          <div className="col-md-6">
            <label className="form-label fw-semibold">{t('autoModel', language)}</label>
            <select
              className="form-select"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            >
              <option value="">{t('autoDefaultModel', language)}</option>
              {Array.isArray(models) &&
                models.map((m: { name: string }) => (
                  <option key={m.name} value={m.name}>
                    {m.name}
                  </option>
                ))}
            </select>
          </div>

          {/* Workspace Type */}
          <div className="col-12">
            <label className="form-label fw-semibold">{t('autoWorkspaceType', language)}</label>
            <div className="btn-group w-100" role="group">
              <button
                type="button"
                className={`btn btn-outline-primary ${workspaceType === 'local' ? 'active' : ''}`}
                onClick={() => setWorkspaceType('local')}
              >
                <i className="bi bi-laptop me-1"></i>
                {t('autoLocalWorkspace', language)}
              </button>
              <button
                type="button"
                className={`btn btn-outline-primary ${workspaceType === 'remote' ? 'active' : ''}`}
                onClick={() => setWorkspaceType('remote')}
              >
                <i className="bi bi-cloud me-1"></i>
                {t('autoRemoteWorkspace', language)}
              </button>
            </div>
          </div>

          {/* Remote Machine */}
          {workspaceType === 'remote' && (
            <div className="col-12">
              <label className="form-label fw-semibold">
                {t('autoRemoteMachine', language)} <span className="text-danger">*</span>
              </label>
              <RemoteMachineSelector
                selectedMachineId={selectedMachineId}
                onSelectMachine={(machineId, machine) => {
                  if (selectedMachineId !== machineId) {
                    setProjectPath('');
                  }
                  setSelectedMachineId(machineId);
                  setSelectedMachineOsType(machine?.os_type ?? '');
                  setSelectedMachineWorkDir(machine?.work_dir ?? '');
                }}
              />
            </div>
          )}

          {/* Project Path */}
          <div className="col-12">
            <div className="form-check mb-2">
              <input
                type="checkbox"
                className="form-check-input"
                id="isNewProject"
                checked={isNewProject}
                onChange={(e) => setIsNewProject(e.target.checked)}
              />
              <label className="form-check-label" htmlFor="isNewProject">
                {t('autoNewProject', language)}
              </label>
            </div>
            {isNewProject ? (
              <div className="row g-2">
                <div className="col-md-8">
                  <input
                    type="text"
                    className="form-control"
                    placeholder={t('autoRepoNamePlaceholder', language)}
                    value={repoName}
                    onChange={(e) => setRepoName(e.target.value)}
                  />
                </div>
                <div className="col-md-4">
                  <div className="form-check">
                    <input
                      type="checkbox"
                      className="form-check-input"
                      id="isPrivate"
                      checked={isPrivate}
                      onChange={(e) => setIsPrivate(e.target.checked)}
                    />
                    <label className="form-check-label" htmlFor="isPrivate">
                      {t('autoPrivateRepo', language)}
                    </label>
                  </div>
                </div>
              </div>
            ) : (
              <div className="input-group">
                <input
                  type="text"
                  className="form-control"
                  placeholder={t('autoProjectPathPlaceholder', language)}
                  value={projectPath}
                  onChange={(e) => setProjectPath(e.target.value)}
                />
                <Button
                  variant="outline-secondary"
                  onClick={() => {
                    if (workspaceType === 'remote') {
                      setShowRemoteDirectoryBrowser(true);
                    } else {
                      setShowLocalDirectoryBrowser(true);
                    }
                  }}
                  disabled={workspaceType === 'remote' && !selectedMachineId}
                >
                  <i className="bi bi-folder2-open me-1" />
                  {t('browse', language) || 'Browse'}
                </Button>
              </div>
            )}
          </div>

          {/* Branch Strategy */}
          <div className="col-md-6">
            <label className="form-label fw-semibold">{t('autoBranchStrategy', language)}</label>
            <select
              className="form-select"
              value={branchStrategy}
              onChange={(e) =>
                setBranchStrategy(e.target.value as 'new-branch' | 'worktree' | 'current')
              }
            >
              <option value="new-branch">{t('autoNewBranch', language)}</option>
              <option value="worktree">{t('autoWorktree', language)}</option>
              <option value="current">{t('autoCurrentBranch', language)}</option>
            </select>
          </div>
          <div className="col-md-6">
            <label className="form-label fw-semibold">{t('autoBranchName', language)}</label>
            <input
              type="text"
              className="form-control"
              placeholder={t('autoBranchNamePlaceholder', language)}
              value={branchName}
              onChange={(e) => setBranchName(e.target.value)}
            />
          </div>

          {/* Review Rounds */}
          <div className="col-md-6">
            <label className="form-label fw-semibold">
              {t('autoMaxPlanRounds', language)}: {maxPlanRounds}
            </label>
            <input
              type="range"
              className="form-range"
              min={1}
              max={5}
              value={maxPlanRounds}
              onChange={(e) => setMaxPlanRounds(parseInt(e.target.value))}
            />
          </div>
          <div className="col-md-6">
            <label className="form-label fw-semibold">
              {t('autoMaxPRReviewRounds', language)}: {maxPRReviewRounds}
            </label>
            <input
              type="range"
              className="form-range"
              min={1}
              max={10}
              value={maxPRReviewRounds}
              onChange={(e) => setMaxPRReviewRounds(parseInt(e.target.value))}
            />
          </div>

          {/* Auto Merge - only show for batch workflows (URL mode with multiple issues) */}
          {requirementsMode === 'url' && requirementsUrl.trim() && (
            <div className="col-12">
              <div className="form-check">
                <input
                  type="checkbox"
                  className="form-check-input"
                  id="autoMerge"
                  checked={autoMerge}
                  onChange={(e) => setAutoMerge(e.target.checked)}
                />
                <label className="form-check-label" htmlFor="autoMerge">
                  {t('autoMergeAfterPR', language) || 'Auto merge after PR created'}
                </label>
                <div className="form-text">
                  {t('autoMergeHint', language) ||
                    'Automatically merge PR and proceed to next workflow in batch'}
                </div>
              </div>
            </div>
          )}
        </div>
      </Modal>

      <LocalDirectoryBrowserModal
        isOpen={showLocalDirectoryBrowser}
        onClose={() => setShowLocalDirectoryBrowser(false)}
        initialPath={projectPath}
        onSelectPath={(path) => {
          setProjectPath(path);
          persistLastProjectPath(path, 'local');
        }}
      />

      {selectedMachineId && (
        <DirectoryBrowserModal
          isOpen={showRemoteDirectoryBrowser}
          onClose={() => setShowRemoteDirectoryBrowser(false)}
          machineId={selectedMachineId}
          initialPath={projectPath}
          osType={selectedMachineOsType || undefined}
          onSelectPath={(path) => {
            setProjectPath(path);
            persistLastProjectPath(path, 'remote', selectedMachineId);
          }}
        />
      )}
    </>
  );
};
