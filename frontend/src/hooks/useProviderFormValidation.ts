/**
 * useProviderFormValidation Hook - Form validation for SSO Provider forms
 *
 * Features:
 * - URL format validation
 * - Client secret confirmation validation
 * - Field-level error state management
 */

import { useState, useCallback, useMemo } from 'react';

export interface ValidationErrors {
  authorization_url?: string;
  token_url?: string;
  userinfo_url?: string;
  redirect_uri?: string;
  client_secret_confirm?: string;
}

export interface FormData {
  authorization_url?: string;
  token_url?: string;
  userinfo_url?: string;
  redirect_uri?: string;
  client_secret?: string;
  client_secret_confirm?: string;
}

/**
 * Validate URL format - must start with http:// or https://
 */
export const validateUrlFormat = (url: string): boolean => {
  if (!url || url.trim() === '') return true; // Empty is valid (optional field)
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
};

/**
 * Mask Client ID for display - show first 6 and last 4 characters
 */
export const maskClientId = (clientId: string): string => {
  if (!clientId || clientId.length <= 10) return clientId || '';
  return `${clientId.slice(0, 6)}...${clientId.slice(-4)}`;
};

export interface UseProviderFormValidationOptions {
  language?: string;
}

export const useProviderFormValidation = (options?: UseProviderFormValidationOptions) => {
  const [errors, setErrors] = useState<ValidationErrors>({});

  /**
   * Validate a single URL field
   */
  const validateUrl = useCallback((field: keyof ValidationErrors, value: string): boolean => {
    const isValid = validateUrlFormat(value);

    if (!isValid && value.trim() !== '') {
      setErrors((prev) => ({
        ...prev,
        [field]: options?.language === 'zh'
          ? 'URL 必须以 http:// 或 https:// 开头'
          : 'URL must start with http:// or https://',
      }));
      return false;
    } else {
      setErrors((prev) => {
        const newErrors = { ...prev };
        delete newErrors[field];
        return newErrors;
      });
      return true;
    }
  }, [options?.language]);

  /**
   * Validate client secret confirmation
   */
  const validateSecretConfirm = useCallback((secret: string, confirm: string): boolean => {
    if (confirm && secret !== confirm) {
      setErrors((prev) => ({
        ...prev,
        client_secret_confirm: options?.language === 'zh'
          ? '两次输入的密钥不一致'
          : 'Secrets do not match',
      }));
      return false;
    } else {
      setErrors((prev) => {
        const newErrors = { ...prev };
        delete newErrors.client_secret_confirm;
        return newErrors;
      });
      return true;
    }
  }, [options?.language]);

  /**
   * Validate all URL fields at once
   */
  const validateAllUrls = useCallback((data: FormData): boolean => {
    const urlFields: (keyof ValidationErrors)[] = [
      'authorization_url',
      'token_url',
      'userinfo_url',
      'redirect_uri',
    ];

    let allValid = true;
    const newErrors: ValidationErrors = { ...errors };

    urlFields.forEach((field) => {
      const value = data[field] || '';
      if (value.trim() !== '' && !validateUrlFormat(value)) {
        newErrors[field] = options?.language === 'zh'
          ? 'URL 必须以 http:// 或 https:// 开头'
          : 'URL must start with http:// or https://';
        allValid = false;
      } else {
        delete newErrors[field];
      }
    });

    setErrors(newErrors);
    return allValid;
  }, [errors, options?.language]);

  /**
   * Clear all errors
   */
  const clearErrors = useCallback(() => {
    setErrors({});
  }, []);

  /**
   * Clear specific error
   */
  const clearError = useCallback((field: keyof ValidationErrors) => {
    setErrors((prev) => {
      const newErrors = { ...prev };
      delete newErrors[field];
      return newErrors;
    });
  }, []);

  /**
   * Check if there are any errors
   */
  const hasErrors = useMemo(() => Object.keys(errors).length > 0, [errors]);

  return {
    errors,
    validateUrl,
    validateSecretConfirm,
    validateAllUrls,
    clearErrors,
    clearError,
    hasErrors,
  };
};

export default useProviderFormValidation;