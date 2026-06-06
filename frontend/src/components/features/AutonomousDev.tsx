/**
 * AutonomousDev Component - AI Autonomous Development page
 */

import React, { useState, useCallback, useEffect } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Button, Loading, EmptyState } from '@/components/common';
import { AutonomousWorkflowList } from '@/components/work/AutonomousWorkflowList';
import { WorkflowTimeline } from '@/components/work/WorkflowTimeline';
import { NewAutonomousModal } from '@/components/work/NewAutonomousModal';
import { useWorkflow, useWorkflowEvents } from '@/hooks/useAutonomous';
import type { AutonomousWorkflow } from '@/api/autonomous';

export const AutonomousDev: React.FC = () => {
  const language = useLanguage();
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [showNewModal, setShowNewModal] = useState(false);

  // Deep linking: read workflow ID from URL query param on mount
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const wfId = params.get('workflow');
    if (wfId) {
      setSelectedWorkflowId(wfId);
    }
  }, []);

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
    selectedWorkflowId || '',
    !!selectedWorkflowId
  );

  useWorkflowEvents(selectedWorkflowId || '', !!selectedWorkflowId);

  const selectedWorkflow = workflowData?.workflow || null;

  const handleSelectWorkflow = useCallback((workflow: AutonomousWorkflow) => {
    setSelectedWorkflowId(workflow.workflow_id);
    updateUrl(workflow.workflow_id);
  }, [updateUrl]);

  const handleWorkflowCreated = useCallback((workflow: AutonomousWorkflow) => {
    setSelectedWorkflowId(workflow.workflow_id);
    updateUrl(workflow.workflow_id);
    setShowNewModal(false);
  }, [updateUrl]);

  return (
    <div className="d-flex h-100">
      {/* Left Panel - Workflow List */}
      <div className="border-end d-flex flex-column" style={{ width: '320px', minWidth: '280px' }}>
        <div className="d-flex align-items-center justify-content-between p-3 border-bottom">
          <h6 className="mb-0 fw-semibold">
            <i className="bi bi-robot me-2"></i>
            {t('autonomousDev', language)}
          </h6>
          <Button size="sm" onClick={() => setShowNewModal(true)}>
            <i className="bi bi-plus-lg me-1"></i>
            {t('autoNewTask', language)}
          </Button>
        </div>
        <div className="flex-grow-1 overflow-auto">
          <AutonomousWorkflowList
            selectedId={selectedWorkflowId}
            onSelect={handleSelectWorkflow}
          />
        </div>
      </div>

      {/* Right Panel - Timeline */}
      <div className="flex-grow-1 d-flex flex-column overflow-hidden">
        {selectedWorkflow ? (
          <WorkflowTimeline workflow={selectedWorkflow} />
        ) : selectedWorkflowId && workflowLoading ? (
          <div className="d-flex align-items-center justify-content-center h-100">
            <Loading />
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
