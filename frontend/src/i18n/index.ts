/**
 * i18n Module - Internationalization support
 */

import type { Language as LanguageType } from '@/types';

// Re-export Language type
export type Language = LanguageType;

type TranslationKey = string;
type Translations = Record<TranslationKey, string>;

const translations: Record<Language, Translations> = {
  en: {
    // Common
    loading: 'Loading...',
    error: 'Error',
    retry: 'Retry',
    refresh: 'Refresh',
    save: 'Save',
    cancel: 'Cancel',
    delete: 'Delete',
    edit: 'Edit',
    close: 'Close',
    search: 'Search',
    filter: 'Filter',
    reset: 'Reset',
    apply: 'Apply',
    noData: 'No data available',
    noDataAvailable: 'No data available',

    // Navigation
    dashboard: 'Dashboard',
    messages: 'Messages',
    analysis: 'Analysis',
    management: 'Management',
    sessions: 'Sessions',
    prompts: 'Prompts',
    report: 'Report',
    workspace: 'Workspace',
    security: 'Security',

    // Dashboard
    dashboardTitle: 'Dashboard',
    todayUsage: "Today's Usage",
    totalOverview: 'Total Overview',
    trendChart: 'Trend Chart',
    dashboardTotalTokens: 'Total Tokens',
    tokenDistribution: 'Token Distribution',
    dashboardFilterAllHosts: 'All Hosts',
    dashboardFilterAllTools: 'All Tools',
    dashboardFilterOpenclaw: 'OpenClaw',
    dashboardFilterClaude: 'Claude',
    dashboardFilterQwen: 'Qwen',

    // Tokens
    tokens: 'tokens',
    inputTokens: 'Input Tokens',
    outputTokens: 'Output Tokens',

    // Table
    tableDate: 'Date',
    tableRequests: 'Requests',
    tableInput: 'Input',
    tableOutput: 'Output',
    tableTotal: 'Total',
    tableTool: 'Tool',
    tableHost: 'Host',
    tableUser: 'User',
    tableSession: 'Session',
    tableMessage: 'Message',
    tableMessages: 'Messages',
    tableTokens: 'Tokens',
    tableSender: 'Sender',
    tableRole: 'Role',
    tableContent: 'Content',
    tableTimestamp: 'Timestamp',

    // Analysis
    overview: 'Overview',
    conversationHistory: 'Conversation History',
    lastMessageTime: 'Last Message',
    conversationDetails: 'Conversation Details',
    conversations: 'conversations',

    // Stats
    days_tracked: 'days tracked',
    avg: 'Avg',
    date_range: 'Date Range',

    // Errors
    errorLoadingToday: "Error loading today's data",
    errorLoadingSummary: 'Error loading summary data',
    tokenDataNotAvailable: 'Token data not available',

    // Auth
    login: 'Login',
    logout: 'Logout',
    username: 'Username',
    password: 'Password',
    loginError: 'Invalid username or password',

    // Theme
    lightTheme: 'Light',
    darkTheme: 'Dark',
    toggleTheme: 'Toggle Theme',

    // Language
    english: 'English',
    chinese: 'Chinese',
    japanese: 'Japanese',
    korean: 'Korean',
  },
  zh: {
    // Common
    loading: '加载中...',
    error: '错误',
    retry: '重试',
    refresh: '刷新',
    save: '保存',
    cancel: '取消',
    delete: '删除',
    edit: '编辑',
    close: '关闭',
    search: '搜索',
    filter: '筛选',
    reset: '重置',
    apply: '应用',
    noData: '暂无数据',
    noDataAvailable: '暂无数据',

    // Navigation
    dashboard: '仪表盘',
    messages: '消息',
    analysis: '分析',
    management: '管理',
    sessions: '会话',
    prompts: '提示词',
    report: '报告',
    workspace: '工作区',
    security: '安全',

    // Dashboard
    dashboardTitle: '仪表盘',
    todayUsage: '今日使用量',
    totalOverview: '总览',
    trendChart: '趋势图',
    dashboardTotalTokens: '总 Token 数',
    tokenDistribution: 'Token 分布',
    dashboardFilterAllHosts: '所有主机',
    dashboardFilterAllTools: '所有工具',
    dashboardFilterOpenclaw: 'OpenClaw',
    dashboardFilterClaude: 'Claude',
    dashboardFilterQwen: 'Qwen',

    // Tokens
    tokens: 'tokens',
    inputTokens: '输入 Tokens',
    outputTokens: '输出 Tokens',

    // Table
    tableDate: '日期',
    tableRequests: '请求数',
    tableInput: '输入',
    tableOutput: '输出',
    tableTotal: '总计',
    tableTool: '工具',
    tableHost: '主机',
    tableUser: '用户',
    tableSession: '会话',
    tableMessage: '消息',
    tableMessages: '消息数',
    tableTokens: 'Tokens',
    tableSender: '发送者',
    tableRole: '角色',
    tableContent: '内容',
    tableTimestamp: '时间戳',

    // Analysis
    overview: '概览',
    conversationHistory: '对话历史',
    lastMessageTime: '最后消息',
    conversationDetails: '对话详情',
    conversations: '个对话',

    // Stats
    days_tracked: '天记录',
    avg: '平均',
    date_range: '日期范围',

    // Errors
    errorLoadingToday: '加载今日数据失败',
    errorLoadingSummary: '加载摘要数据失败',
    tokenDataNotAvailable: 'Token 数据不可用',

    // Auth
    login: '登录',
    logout: '退出登录',
    username: '用户名',
    password: '密码',
    loginError: '用户名或密码错误',

    // Theme
    lightTheme: '浅色',
    darkTheme: '深色',
    toggleTheme: '切换主题',

    // Language
    english: '英语',
    chinese: '中文',
    japanese: '日语',
    korean: '韩语',
  },
  ja: {
    // Common
    loading: '読み込み中...',
    error: 'エラー',
    retry: '再試行',
    refresh: '更新',
    save: '保存',
    cancel: 'キャンセル',
    delete: '削除',
    edit: '編集',
    close: '閉じる',
    search: '検索',
    filter: 'フィルター',
    reset: 'リセット',
    apply: '適用',
    noData: 'データがありません',
    noDataAvailable: 'データがありません',

    // Navigation
    dashboard: 'ダッシュボード',
    messages: 'メッセージ',
    analysis: '分析',
    management: '管理',
    sessions: 'セッション',
    prompts: 'プロンプト',
    report: 'レポート',
    workspace: 'ワークスペース',
    security: 'セキュリティ',

    // Dashboard
    dashboardTitle: 'ダッシュボード',
    todayUsage: '今日の使用量',
    totalOverview: '概要',
    trendChart: 'トレンドチャート',
    dashboardTotalTokens: '合計トークン数',
    dashboardFilterAllHosts: 'すべてのホスト',
    dashboardFilterAllTools: 'すべてのツール',
    dashboardFilterOpenclaw: 'OpenClaw',
    dashboardFilterClaude: 'Claude',
    dashboardFilterQwen: 'Qwen',

    // Tokens
    tokens: 'tokens',
    inputTokens: '入力トークン',
    outputTokens: '出力トークン',

    // Table
    tableDate: '日付',
    tableRequests: 'リクエスト数',
    tableInput: '入力',
    tableOutput: '出力',
    tableTotal: '合計',
    tableTool: 'ツール',
    tableHost: 'ホスト',
    tableUser: 'ユーザー',
    tableSession: 'セッション',
    tableMessage: 'メッセージ',
    tableMessages: 'メッセージ数',
    tableTokens: 'トークン',
    tableSender: '送信者',
    tableRole: '役割',
    tableContent: '内容',
    tableTimestamp: 'タイムスタンプ',

    // Analysis
    overview: '概要',
    conversationHistory: '会話履歴',
    lastMessageTime: '最終メッセージ',
    conversationDetails: '会話詳細',
    conversations: '件の会話',

    // Stats
    days_tracked: '日間記録',
    avg: '平均',
    date_range: '日付範囲',

    // Errors
    errorLoadingToday: '今日のデータの読み込みエラー',
    errorLoadingSummary: 'サマリーデータの読み込みエラー',
    tokenDataNotAvailable: 'トークンデータがありません',

    // Auth
    login: 'ログイン',
    logout: 'ログアウト',
    username: 'ユーザー名',
    password: 'パスワード',
    loginError: 'ユーザー名またはパスワードが無効です',

    // Theme
    lightTheme: 'ライト',
    darkTheme: 'ダーク',
    toggleTheme: 'テーマ切替',

    // Language
    english: '英語',
    chinese: '中国語',
    japanese: '日本語',
    korean: '韓国語',
  },
  ko: {
    // Common
    loading: '로딩 중...',
    error: '오류',
    retry: '재시도',
    refresh: '새로고침',
    save: '저장',
    cancel: '취소',
    delete: '삭제',
    edit: '편집',
    close: '닫기',
    search: '검색',
    filter: '필터',
    reset: '초기화',
    apply: '적용',
    noData: '데이터 없음',
    noDataAvailable: '데이터 없음',

    // Navigation
    dashboard: '대시보드',
    messages: '메시지',
    analysis: '분석',
    management: '관리',
    sessions: '세션',
    prompts: '프롬프트',
    report: '보고서',
    workspace: '워크스페이스',
    security: '보안',

    // Dashboard
    dashboardTitle: '대시보드',
    todayUsage: '오늘 사용량',
    totalOverview: '개요',
    trendChart: '트렌드 차트',
    dashboardTotalTokens: '총 토큰 수',
    dashboardFilterAllHosts: '모든 호스트',
    dashboardFilterAllTools: '모든 도구',
    dashboardFilterOpenclaw: 'OpenClaw',
    dashboardFilterClaude: 'Claude',
    dashboardFilterQwen: 'Qwen',

    // Tokens
    tokens: 'tokens',
    inputTokens: '입력 토큰',
    outputTokens: '출력 토큰',

    // Table
    tableDate: '날짜',
    tableRequests: '요청 수',
    tableInput: '입력',
    tableOutput: '출력',
    tableTotal: '합계',
    tableTool: '도구',
    tableHost: '호스트',
    tableUser: '사용자',
    tableSession: '세션',
    tableMessage: '메시지',
    tableMessages: '메시지 수',
    tableTokens: '토큰',
    tableSender: '발신자',
    tableRole: '역할',
    tableContent: '내용',
    tableTimestamp: '타임스탬프',

    // Analysis
    overview: '개요',
    conversationHistory: '대화 기록',
    lastMessageTime: '마지막 메시지',
    conversationDetails: '대화 상세',
    conversations: '개 대화',

    // Stats
    days_tracked: '일 기록',
    avg: '평균',
    date_range: '날짜 범위',

    // Errors
    errorLoadingToday: '오늘 데이터 로드 오류',
    errorLoadingSummary: '요약 데이터 로드 오류',
    tokenDataNotAvailable: '토큰 데이터 없음',

    // Auth
    login: '로그인',
    logout: '로그아웃',
    username: '사용자 이름',
    password: '비밀번호',
    loginError: '사용자 이름 또는 비밀번호가 잘못되었습니다',

    // Theme
    lightTheme: '라이트',
    darkTheme: '다크',
    toggleTheme: '테마 전환',

    // Language
    english: '영어',
    chinese: '중국어',
    japanese: '일본어',
    korean: '한국어',
  },
};

let currentLanguage: Language = 'en';

export function setLanguage(language: Language): void {
  currentLanguage = language;
  localStorage.setItem('language', language);
}

export function getLanguage(): Language {
  return currentLanguage;
}

export function t(key: string, language?: Language): string {
  const lang = language || currentLanguage;
  const langTranslations = translations[lang] || translations.en;
  return langTranslations[key] || key;
}

export function initLanguage(): void {
  const savedLanguage = localStorage.getItem('language') as Language | null;
  const browserLanguage = navigator.language.split('-')[0] as Language;

  if (savedLanguage && translations[savedLanguage]) {
    currentLanguage = savedLanguage;
  } else if (translations[browserLanguage]) {
    currentLanguage = browserLanguage;
  } else {
    currentLanguage = 'en';
  }
}

// Initialize language on module load
initLanguage();
