/**
 * PasswordPolicyHint - Display password policy requirements
 */

import React from 'react';
import { useLanguage } from '@/store';
import { usePasswordPolicy } from '@/hooks';
import { t } from '@/i18n';

export const PasswordPolicyHint: React.FC = () => {
  const language = useLanguage();
  const { data: policy } = usePasswordPolicy();

  if (!policy) return null;

  const requirements: string[] = [];
  requirements.push(`${t('passwordMinLength', language)}: ${policy.password_min_length ?? 8}`);
  if (policy.password_require_uppercase) requirements.push(t('requireUppercase', language));
  if (policy.password_require_lowercase) requirements.push(t('requireLowercase', language));
  if (policy.password_require_number) requirements.push(t('requireNumber', language));
  if (policy.password_require_special) requirements.push(t('requireSpecial', language));

  return (
    <div className="password-policy-hint text-muted small mt-1">
      <div>{t('passwordRequirements', language)}:</div>
      <ul className="mb-0 ps-3" style={{ fontSize: '0.85em' }}>
        {requirements.map((req, idx) => (
          <li key={idx}>{req}</li>
        ))}
      </ul>
    </div>
  );
};
