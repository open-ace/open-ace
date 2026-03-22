/**
 * Management Component - Admin management page with tabs
 */

import React, { useState } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Card } from '@/components/common';
import { UserManagement } from './management/UserManagement';
import { QuotaManagement } from './management/QuotaManagement';
import { AuditLog } from './management/AuditLog';
import { ContentFilter } from './management/ContentFilter';
import { SecuritySettings } from './management/SecuritySettings';

export const Management: React.FC = () => {
  const language = useLanguage();
  const [activeTab, setActiveTab] = useState('users');

  const tabs = [
    { id: 'users', label: t('userManagement', language), icon: 'bi-people' },
    { id: 'quota', label: t('quotaManagement', language), icon: 'bi-sliders' },
    { id: 'audit', label: t('auditLog', language), icon: 'bi-journal-text' },
    { id: 'filter', label: t('contentFilter', language), icon: 'bi-shield-check' },
    { id: 'security', label: t('securitySettings', language), icon: 'bi-lock' },
  ];

  const renderContent = () => {
    switch (activeTab) {
      case 'users':
        return <UserManagement />;
      case 'quota':
        return <QuotaManagement />;
      case 'audit':
        return <AuditLog />;
      case 'filter':
        return <ContentFilter />;
      case 'security':
        return <SecuritySettings />;
      default:
        return <UserManagement />;
    }
  };

  return (
    <div className="management">
      {/* Header */}
      <div className="management-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('management', language)}</h2>
      </div>

      {/* Tabs */}
      <Card className="mb-4">
        <div className="nav nav-tabs" role="tablist">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={`nav-link ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
              type="button"
              role="tab"
            >
              <i className={`bi ${tab.icon} me-2`} />
              {tab.label}
            </button>
          ))}
        </div>
      </Card>

      {/* Content */}
      <div className="management-content">{renderContent()}</div>
    </div>
  );
};
