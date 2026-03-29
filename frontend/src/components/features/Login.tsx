/**
 * Login Page Component
 */

import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '@/store';
import { useAuth } from '@/hooks';
import { Button } from '@/components/common';
import type { Language } from '@/types';
import './Login.css';

// Translations
const translations: Record<Language, Record<string, string>> = {
  en: {
    title: 'Open ACE',
    subtitle: 'Please sign in to continue',
    username: 'Username',
    password: 'Password',
    usernamePlaceholder: 'Enter your username',
    passwordPlaceholder: 'Enter your password',
    signIn: 'Sign In',
    signingIn: 'Signing in...',
    loginSuccess: 'Login successful! Redirecting...',
    invalidCredentials: 'Invalid username or password',
    errorOccurred: 'An error occurred',
    defaultCredentials: 'Default admin credentials:',
    changePasswordNotice: 'Change the default password after first login!',
    copyright: '© 2026 Open ACE. All rights reserved.',
  },
  zh: {
    title: 'Open ACE',
    subtitle: '请登录以继续',
    username: '用户名',
    password: '密码',
    usernamePlaceholder: '请输入用户名',
    passwordPlaceholder: '请输入密码',
    signIn: '登录',
    signingIn: '登录中...',
    loginSuccess: '登录成功！正在跳转...',
    invalidCredentials: '用户名或密码错误',
    errorOccurred: '发生错误',
    defaultCredentials: '默认管理员账号：',
    changePasswordNotice: '首次登录后请修改默认密码！',
    copyright: '© 2026 Open ACE. 保留所有权利。',
  },
  ja: {
    title: 'Open ACE',
    subtitle: '続行するにはサインインしてください',
    username: 'ユーザー名',
    password: 'パスワード',
    usernamePlaceholder: 'ユーザー名を入力',
    passwordPlaceholder: 'パスワードを入力',
    signIn: 'サインイン',
    signingIn: 'サインイン中...',
    loginSuccess: 'ログイン成功！リダイレクト中...',
    invalidCredentials: 'ユーザー名またはパスワードが無効です',
    errorOccurred: 'エラーが発生しました',
    defaultCredentials: 'デフォルトの管理者認証情報：',
    changePasswordNotice: '初回ログイン後にデフォルトパスワードを変更してください！',
    copyright: '© 2026 Open ACE. All rights reserved.',
  },
  ko: {
    title: 'Open ACE',
    subtitle: '계속하려면 로그인하세요',
    username: '사용자 이름',
    password: '비밀번호',
    usernamePlaceholder: '사용자 이름 입력',
    passwordPlaceholder: '비밀번호 입력',
    signIn: '로그인',
    signingIn: '로그인 중...',
    loginSuccess: '로그인 성공! 리디렉션 중...',
    invalidCredentials: '사용자 이름 또는 비밀번호가 잘못되었습니다',
    errorOccurred: '오류가 발생했습니다',
    defaultCredentials: '기본 관리자 자격 증명:',
    changePasswordNotice: '첫 로그인 후 기본 비밀번호를 변경하세요!',
    copyright: '© 2026 Open ACE. All rights reserved.',
  },
};

function getTranslation(key: string, language: Language): string {
  return translations[language]?.[key] || translations.en[key] || key;
}

export const Login: React.FC = () => {
  const navigate = useNavigate();
  const { language, setLanguage } = useAppStore();
  const { login, isAuthenticated } = useAuth();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(false);
  const [showDefaultCredentials, setShowDefaultCredentials] = useState(false);

  // Check if already authenticated
  useEffect(() => {
    if (isAuthenticated) {
      navigate('/');
    }

    // Check if default credentials should be shown (development mode)
    const isDev = import.meta.env.DEV;
    setShowDefaultCredentials(isDev);
  }, [navigate, isAuthenticated]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setLoading(true);

    try {
      const response = await login({ username, password });

      if (response.success && response.user) {
        setSuccess(getTranslation('loginSuccess', language));
        // Redirect after short delay
        setTimeout(() => {
          navigate('/');
        }, 500);
      } else {
        setError(getTranslation('invalidCredentials', language));
      }
    } catch (err: any) {
      setError(err.message ?? getTranslation('errorOccurred', language));
    } finally {
      setLoading(false);
    }
  };

  const handleLanguageChange = (lang: Language) => {
    setLanguage(lang);
  };

  return (
    <div className="login-page">
      {/* Language Selector */}
      <div className="login-lang-selector">
        <select
          value={language}
          onChange={(e) => handleLanguageChange(e.target.value as Language)}
          aria-label="Select language"
        >
          <option value="en">English</option>
          <option value="zh">中文</option>
          <option value="ja">日本語</option>
          <option value="ko">한국어</option>
        </select>
      </div>

      <div className="login-container">
        <div className="login-header">
          <div className="login-logo">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
              <defs>
                <linearGradient id="login-icon-grad" x1="0" x2="1" y1="0" y2="1">
                  <stop offset="0" stopColor="#667eea" />
                  <stop offset="1" stopColor="#764ba2" />
                </linearGradient>
              </defs>
              <rect width="100" height="100" rx="20" fill="url(#login-icon-grad)" />
              <path
                d="M30 40h40M30 60h40M35 30v40M65 30v40M25 50h50"
                stroke="white"
                strokeWidth="8"
                strokeLinecap="round"
                fill="none"
              />
            </svg>
          </div>
          <h1>{getTranslation('title', language)}</h1>
          <p>{getTranslation('subtitle', language)}</p>
        </div>

        {error && <div className="login-error">{error}</div>}
        {success && <div className="login-success">{success}</div>}

        <form onSubmit={handleSubmit} className="login-form">
          <div className="login-form-group">
            <label htmlFor="username">{getTranslation('username', language)}</label>
            <input
              type="text"
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder={getTranslation('usernamePlaceholder', language)}
              autoComplete="username"
              required
              autoFocus
            />
          </div>

          <div className="login-form-group">
            <label htmlFor="password">{getTranslation('password', language)}</label>
            <input
              type="password"
              id="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={getTranslation('passwordPlaceholder', language)}
              autoComplete="current-password"
              required
            />
          </div>

          <Button type="submit" variant="primary" fullWidth loading={loading} disabled={loading}>
            {loading ? getTranslation('signingIn', language) : getTranslation('signIn', language)}
          </Button>
        </form>

        {showDefaultCredentials && (
          <div className="login-default-credentials">
            <p>
              {getTranslation('defaultCredentials', language)} <strong>admin / admin123</strong>
            </p>
            <p className="warning">{getTranslation('changePasswordNotice', language)}</p>
          </div>
        )}

        <div className="login-footer">
          <p>{getTranslation('copyright', language)}</p>
        </div>
      </div>
    </div>
  );
};

export default Login;
