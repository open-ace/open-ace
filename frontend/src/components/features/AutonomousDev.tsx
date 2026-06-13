/**
 * AutonomousDev Component - AI Autonomous Development page
 */

import React, { useState, useCallback, useMemo, useEffect } from 'react';
import { useAppStore, useLanguage, useWorkspaceFullscreen } from '@/store';
import { t } from '@/i18n';
import { Button, Loading, EmptyState } from '@/components/common';
import { AutonomousWorkflowList } from '@/components/work/AutonomousWorkflowList';
import { WorkflowTimeline } from '@/components/work/WorkflowTimeline';
import { NewAutonomousModal } from '@/components/work/NewAutonomousModal';
import { useWorkflow, useWorkflowEvents } from '@/hooks/useAutonomous';
import type { AutonomousWorkflow } from '@/api/autonomous';
import { cn } from '@/utils';
import './AutonomousDev.css';

const LEFT_PANEL_WIDTH_KEY = 'autonomous-dev-left-panel-width';
const DEFAULT_LEFT_PANEL_WIDTH = 360;
const MIN_LEFT_PANEL_WIDTH = 300;
const MAX_LEFT_PANEL_WIDTH = 720;

export const AutonomousDev: React.FC = () => {
  const language = useLanguage();
  const workspaceFullscreen = useWorkspaceFullscreen();
  const { toggleWorkspaceFullscreen } = useAppStore();
  const initialWorkflowId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('workflow');
  }, []);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(initialWorkflowId);
  const [showNewModal, setShowNewModal] = useState(false);
  const [workflowListState, setWorkflowListState] = useState({
    total: 0,
    isLoading: true,
    hasLoaded: false,
    hasActiveFilters: false,
  });
  const [leftPanelWidth, setLeftPanelWidth] = useState(() => {
    if (typeof window === 'undefined') {
      return DEFAULT_LEFT_PANEL_WIDTH;
    }
    const saved = Number(window.localStorage.getItem(LEFT_PANEL_WIDTH_KEY) ?? '');
    return Number.isFinite(saved) && saved >= MIN_LEFT_PANEL_WIDTH && saved <= MAX_LEFT_PANEL_WIDTH
      ? saved
      : DEFAULT_LEFT_PANEL_WIDTH;
  });

  useEffect(() => {
    window.localStorage.setItem(LEFT_PANEL_WIDTH_KEY, String(leftPanelWidth));
  }, [leftPanelWidth]);

  const clampLeftPanelWidth = useCallback((nextWidth: number) => {
    const viewportLimit = Math.max(MIN_LEFT_PANEL_WIDTH, Math.floor(window.innerWidth * 0.55));
    return Math.min(
      Math.max(nextWidth, MIN_LEFT_PANEL_WIDTH),
      Math.min(MAX_LEFT_PANEL_WIDTH, viewportLimit)
    );
  }, []);

  const handleResizeStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = leftPanelWidth;
      const body = document.body;

      body.style.cursor = 'col-resize';
      body.style.userSelect = 'none';

      const handlePointerMove = (moveEvent: PointerEvent) => {
        const deltaX = moveEvent.clientX - startX;
        setLeftPanelWidth(clampLeftPanelWidth(startWidth + deltaX));
      };

      const handlePointerUp = () => {
        body.style.cursor = '';
        body.style.userSelect = '';
        window.removeEventListener('pointermove', handlePointerMove);
        window.removeEventListener('pointerup', handlePointerUp);
      };

      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerup', handlePointerUp);
    },
    [clampLeftPanelWidth, leftPanelWidth]
  );

  // Update URL when selection changes
  const updateUrl = useCallback((workflowId: string | null) => {
    const url = new URL(window.location.href);
    if (workflowId) {
      url.searchParams.set('workflow', workflowId);
    } else {
      url.searchParams.delete('workflow');
    }
    window.history.replaceState({}, '', url.toString());
  }, []);

  const { data: workflowData, isLoading: workflowLoading } = useWorkflow(
    selectedWorkflowId ?? '',
    !!selectedWorkflowId
  );

  useWorkflowEvents(selectedWorkflowId ?? '', !!selectedWorkflowId);

  const selectedWorkflow = workflowData?.workflow ?? null;

  const handleSelectWorkflow = useCallback(
    (workflow: AutonomousWorkflow) => {
      setSelectedWorkflowId(workflow.workflow_id);
      updateUrl(workflow.workflow_id);
    },
    [updateUrl]
  );

  const handleClearWorkflow = useCallback(() => {
    setSelectedWorkflowId(null);
    updateUrl(null);
  }, [updateUrl]);

  const handleListStateChange = useCallback(
    (state: {
      total: number;
      isLoading: boolean;
      hasLoaded: boolean;
      hasActiveFilters: boolean;
      workflows: AutonomousWorkflow[];
    }) => {
      setWorkflowListState((prev) => {
        if (
          prev.total === state.total &&
          prev.isLoading === state.isLoading &&
          prev.hasLoaded === state.hasLoaded &&
          prev.hasActiveFilters === state.hasActiveFilters
        ) {
          return prev;
        }
        return {
          total: state.total,
          isLoading: state.isLoading,
          hasLoaded: state.hasLoaded,
          hasActiveFilters: state.hasActiveFilters,
        };
      });
    },
    []
  );

  const handleWorkflowCreated = useCallback(
    (workflow: AutonomousWorkflow) => {
      setSelectedWorkflowId(workflow.workflow_id);
      updateUrl(workflow.workflow_id);
      setShowNewModal(false);
    },
    [updateUrl]
  );

  return (
    <div
      className={cn(
        'd-flex h-100 autonomous-dev-layout',
        workspaceFullscreen && 'autonomous-dev-layout-fullscreen'
      )}
    >
      {/* Left Panel - Workflow List */}
      <div
        className="border-end d-flex flex-column autonomous-dev-left-panel"
        style={{ width: `${leftPanelWidth}px`, minWidth: `${MIN_LEFT_PANEL_WIDTH}px` }}
      >
        <div className="d-flex align-items-center justify-content-between p-3 border-bottom">
          <h6 className="mb-0 fw-semibold">
            <i className="bi bi-robot me-2"></i>
            {t('autonomousDev', language)}
          </h6>
          <div className="d-flex align-items-center gap-2">
            <button
              className="btn btn-sm btn-outline-secondary autonomous-dev-fullscreen-btn"
              onClick={() => toggleWorkspaceFullscreen(false, false)}
              title={
                workspaceFullscreen ? t('exitFullscreen', language) : t('enterFullscreen', language)
              }
            >
              <i
                className={cn('bi', workspaceFullscreen ? 'bi-fullscreen-exit' : 'bi-fullscreen')}
              />
            </button>
            <Button size="sm" onClick={() => setShowNewModal(true)}>
              <i className="bi bi-plus-lg me-1"></i>
              {t('autoNewTask', language)}
            </Button>
          </div>
        </div>
        <div className="flex-grow-1 overflow-auto autonomous-dev-list-scroll">
          <AutonomousWorkflowList
            selectedId={selectedWorkflowId}
            onSelect={handleSelectWorkflow}
            onClearSelection={handleClearWorkflow}
            preserveInitialSelection={!!initialWorkflowId}
            onListStateChange={handleListStateChange}
          />
        </div>
      </div>
      <div
        className="autonomous-dev-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize workflow panels"
        onPointerDown={handleResizeStart}
      />

      {/* Right Panel - Timeline */}
      <div className="flex-grow-1 d-flex flex-column overflow-hidden autonomous-dev-right-panel">
        {selectedWorkflow ? (
          <WorkflowTimeline
            workflow={selectedWorkflow}
            onNavigateToWorkflow={(id) => {
              setSelectedWorkflowId(id);
              updateUrl(id);
            }}
          />
        ) : selectedWorkflowId && workflowLoading ? (
          <div className="d-flex align-items-center justify-content-center h-100">
            <Loading />
          </div>
        ) : !workflowListState.hasLoaded || workflowListState.isLoading ? (
          <div className="d-flex align-items-center justify-content-center h-100">
            <Loading />
          </div>
        ) : workflowListState.total > 0 ? (
          <div className="d-flex align-items-center justify-content-center h-100">
            <EmptyState
              icon="bi-list-check"
              title={t('autonomousDev', language)}
              description={t('autoSelectWorkflowPrompt', language)}
            />
          </div>
        ) : workflowListState.hasActiveFilters ? (
          <div className="d-flex align-items-center justify-content-center h-100">
            <EmptyState
              icon="bi-search"
              title={t('autonomousDev', language)}
              description={t('autoNoMatchingWorkflows', language)}
            />
          </div>
        ) : (
          <div className="d-flex align-items-center justify-content-center h-100">
            <EmptyState
              icon="bi-robot"
              title={t('autonomousDev', language)}
              description={t('autonomousDevEmpty', language)}
              action={
                <Button onClick={() => setShowNewModal(true)}>
                  <i className="bi bi-plus-lg me-1"></i>
                  {t('autoCreateFirstTask', language)}
                </Button>
              }
            />
          </div>
        )}
      </div>

      {/* New Workflow Modal */}
      <NewAutonomousModal
        show={showNewModal}
        onClose={() => setShowNewModal(false)}
        onCreated={handleWorkflowCreated}
      />
    </div>
  );
};
