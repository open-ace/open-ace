/**
 * FilterRuleTableHeader Component - Shared table header for content filter rules
 */

import React from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Tooltip } from '@/components/common';

export const FilterRuleTableHeader: React.FC = () => {
  const language = useLanguage();

  return (
    <thead>
      <tr>
        <th>
          {t('tablePattern', language)}
          <Tooltip content={t('patternHelp', language)} placement="top">
            <i
              className="bi bi-question-circle text-muted ms-1"
              style={{ cursor: 'pointer' }}
              aria-label={t('patternHelp', language)}
            />
          </Tooltip>
        </th>
        <th>
          {t('tableType', language)}
          <Tooltip
            content={
              <div className="d-flex flex-column gap-1">
                <div>{t('keywordTypeHelp', language)}</div>
                <div>{t('regexTypeHelp', language)}</div>
                <div>{t('piiTypeHelp', language)}</div>
              </div>
            }
            placement="top"
          >
            <i
              className="bi bi-question-circle text-muted ms-1"
              style={{ cursor: 'pointer' }}
              aria-label={`${t('keywordTypeHelp', language)}, ${t('regexTypeHelp', language)}, ${t('piiTypeHelp', language)}`}
            />
          </Tooltip>
        </th>
        <th>{t('tableSeverity', language)}</th>
        <th>
          {t('tableAction', language)}
          <Tooltip
            content={
              <div className="d-flex flex-column gap-1">
                <div>{t('warnActionHelp', language)}</div>
                <div>{t('blockActionHelp', language)}</div>
                <div>{t('redactActionHelp', language)}</div>
              </div>
            }
            placement="top"
          >
            <i
              className="bi bi-question-circle text-muted ms-1"
              style={{ cursor: 'pointer' }}
              aria-label={`${t('warnActionHelp', language)}, ${t('blockActionHelp', language)}, ${t('redactActionHelp', language)}`}
            />
          </Tooltip>
        </th>
        <th>{t('tableStatus', language)}</th>
        <th>{t('tableActions', language)}</th>
      </tr>
    </thead>
  );
};
