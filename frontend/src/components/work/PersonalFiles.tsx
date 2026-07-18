/**
 * PersonalFiles Component - direct entry for browsing the current user's home workspace.
 */

import React, { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { LocalDirectoryBrowser } from './LocalDirectoryBrowser';

export const PersonalFiles: React.FC = () => {
  const language = useLanguage();
  const navigate = useNavigate();

  const handleSelectPath = useCallback(
    (path: string) => {
      const params = new URLSearchParams({
        newTab: 'true',
        workspaceType: 'local',
        projectPath: path,
      });
      navigate(`/work?${params.toString()}`);
    },
    [navigate]
  );

  return (
    <div className="personal-files">
      <div className="personal-files-header">
        <h1>{t('personalFiles', language)}</h1>
      </div>
      <div className="personal-files-browser">
        <LocalDirectoryBrowser
          initialPath="home"
          onSelectPath={handleSelectPath}
          listMaxHeight="min(56vh, 620px)"
        />
      </div>
    </div>
  );
};
