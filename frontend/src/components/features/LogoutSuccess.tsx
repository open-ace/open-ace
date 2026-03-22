/**
 * Logout Success Page Component
 */

import React, { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '@/store';
import { t } from '@/i18n';
import { Button } from '@/components/common';
import './LogoutSuccess.css';

export const LogoutSuccess: React.FC = () => {
  const navigate = useNavigate();
  const { language } = useAppStore();

  useEffect(() => {
    // Auto redirect to login after 3 seconds
    const timer = setTimeout(() => {
      navigate('/login');
    }, 3000);

    return () => clearTimeout(timer);
  }, [navigate]);

  const handleLoginClick = () => {
    navigate('/login');
  };

  return (
    <div className="logout-success-page">
      <div className="logout-success-container">
        <div className="logout-success-icon">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
        </div>

        <h1>{t('logout', language)}</h1>
        <p className="logout-message">
          {language === 'zh'
            ? '您已成功登出。感谢使用 Open ACE！'
            : language === 'ja'
              ? 'ログアウトしました。Open ACEをご利用いただきありがとうございました！'
              : language === 'ko'
                ? '로그아웃되었습니다. Open ACE를 이용해 주셔서 감사합니다!'
                : 'You have been successfully logged out. Thank you for using Open ACE!'}
        </p>

        <p className="logout-redirect">
          {language === 'zh'
            ? '将在 3 秒后跳转到登录页面...'
            : language === 'ja'
              ? '3秒後にログインページにリダイレクトします...'
              : language === 'ko'
                ? '3초 후 로그인 페이지로 이동합니다...'
                : 'Redirecting to login page in 3 seconds...'}
        </p>

        <Button variant="primary" onClick={handleLoginClick}>
          {t('login', language)}
        </Button>
      </div>
    </div>
  );
};

export default LogoutSuccess;
