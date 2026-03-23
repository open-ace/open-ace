/**
 * Open ACE Frontend - Type Definitions
 */

// User types
export interface User {
  id: string;
  username: string;
  email: string;
  role: 'admin' | 'user' | 'viewer';
  createdAt: string;
  lastLogin?: string;
}

// Authentication types
export interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
}

// Tool usage types
export interface ToolUsage {
  tool_name: string;
  date: string;
  tokens_used: number;
  input_tokens: number;
  output_tokens: number;
  request_count: number;
  host?: string;
}

// Summary data types
export interface ToolSummary {
  total_tokens: number;
  total_requests: number;
  total_input_tokens?: number;
  total_output_tokens?: number;
  days_count: number;
  avg_tokens: number;
  first_date: string;
  last_date: string;
}

export interface SummaryData {
  [tool: string]: ToolSummary;
}

// Message types
export interface Message {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  tokens?: number;
  input_tokens?: number;
  output_tokens?: number;
  timestamp: string;
  tool_name?: string;
  host?: string;
  host_name?: string;
  sender_name?: string;
  sender_id?: string;
  model?: string;
  message_source?: string;
  full_entry?: Record<string, unknown>;
}

export interface MessageFilters {
  tool?: string;
  host?: string;
  sender?: string;
  startDate?: string;
  endDate?: string;
  role?: string[];
  search?: string;
}

// Session types
export interface Session {
  id: string;
  user_id: string;
  tool_name: string;
  host?: string;
  started_at: string;
  ended_at?: string;
  message_count: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
}

export interface SessionFilters {
  tool?: string;
  host?: string;
  startDate?: string;
  endDate?: string;
  userId?: string;
}

// Analysis types
export interface AnalysisMetrics {
  totalSessions: number;
  totalMessages: number;
  totalTokens: number;
  avgTokensPerSession: number;
  avgMessagesPerSession: number;
  topTools: Array<{ tool: string; count: number }>;
  topHosts: Array<{ host: string; count: number }>;
  dailyTrend: Array<{ date: string; tokens: number; requests: number }>;
}

export interface AnalysisFilters {
  startDate?: string;
  endDate?: string;
  tool?: string;
  host?: string;
  groupBy?: 'day' | 'week' | 'month';
}

// Admin types
export interface QuotaUsage {
  user_id: string;
  username: string;
  tokens_used: number;
  tokens_limit: number;
  percentage: number;
  period_start: string;
  period_end: string;
}

export interface AuditLog {
  id: string;
  user_id: string;
  action: string;
  resource: string;
  details: Record<string, unknown>;
  timestamp: string;
  ip_address?: string;
}

// Governance types
export interface ContentCheckResult {
  id: string;
  content: string;
  result: 'approved' | 'rejected' | 'pending';
  reason?: string;
  checked_at: string;
  checked_by: string;
}

export interface FilterStats {
  total_checks: number;
  approved: number;
  rejected: number;
  pending: number;
  top_reasons: Array<{ reason: string; count: number }>;
}

// Chart types
export interface ChartDataset {
  label: string;
  data: number[];
  backgroundColor?: string | string[];
  borderColor?: string | string[];
  borderWidth?: number;
  fill?: boolean;
  tension?: number;
}

export interface ChartData {
  labels: string[];
  datasets: ChartDataset[];
}

// API Response types
export interface ApiResponse<T> {
  data: T;
  success: boolean;
  message?: string;
  pagination?: {
    page: number;
    pageSize: number;
    total: number;
    totalPages: number;
  };
}

export interface ApiError {
  message: string;
  code?: string;
  status?: number;
  details?: Record<string, unknown>;
}

// Theme types
export type Theme = 'light' | 'dark';

// Language types
export type Language = 'en' | 'zh' | 'ja' | 'ko';

// App Mode types - Dual-track system
export type AppMode = 'work' | 'manage';

// App state types
export interface AppState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  theme: Theme;
  language: Language;
  sidebarCollapsed: boolean;
}

// Component prop types
export interface BaseComponentProps {
  className?: string;
  id?: string;
  'data-testid'?: string;
}

export interface LoadingProps extends BaseComponentProps {
  size?: 'sm' | 'md' | 'lg';
  text?: string;
}

export interface ErrorProps extends BaseComponentProps {
  message: string;
  onRetry?: () => void;
}

// Pagination types
export interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
}

// Table types
export interface TableColumn<T> {
  key: keyof T | string;
  header: string;
  sortable?: boolean;
  render?: (value: unknown, row: T) => React.ReactNode;
  width?: string | number;
  align?: 'left' | 'center' | 'right';
}

export interface TableProps<T> extends BaseComponentProps {
  columns: TableColumn<T>[];
  data: T[];
  loading?: boolean;
  error?: string;
  pagination?: PaginationProps;
  onRowClick?: (row: T) => void;
  emptyMessage?: string;
}

// Modal types
export interface ModalProps extends BaseComponentProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  children: React.ReactNode;
  footer?: React.ReactNode;
}

// Card types
export interface CardProps extends BaseComponentProps {
  title?: string;
  subtitle?: string;
  icon?: React.ReactNode;
  variant?: 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info';
  children: React.ReactNode;
  footer?: React.ReactNode;
}

// Button types
export type ButtonVariant =
  | 'primary'
  | 'secondary'
  | 'success'
  | 'danger'
  | 'warning'
  | 'info'
  | 'light'
  | 'dark'
  | 'link'
  | 'outline-primary'
  | 'outline-secondary'
  | 'outline-success'
  | 'outline-danger'
  | 'outline-warning'
  | 'outline-info'
  | 'outline-light'
  | 'outline-dark';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends BaseComponentProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  disabled?: boolean;
  loading?: boolean;
  onClick?: () => void;
  type?: 'button' | 'submit' | 'reset';
  children: React.ReactNode;
  icon?: React.ReactNode;
  fullWidth?: boolean;
}

// Stat Card types
export interface StatCardProps extends BaseComponentProps {
  label: string;
  value: number | string;
  icon?: React.ReactNode;
  trend?: {
    value: number;
    isPositive: boolean;
  };
  variant?: 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info';
}

// Select types
export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends BaseComponentProps {
  options: SelectOption[];
  value?: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  size?: 'sm' | 'md' | 'lg';
  style?: React.CSSProperties;
}

// Date picker types
export interface DatePickerProps extends BaseComponentProps {
  value?: string;
  onChange: (value: string) => void;
  min?: string;
  max?: string;
  placeholder?: string;
  disabled?: boolean;
}

// Filter types
export interface FilterBarProps extends BaseComponentProps {
  filters: Record<string, unknown>;
  onFilterChange: (filters: Record<string, unknown>) => void;
  onReset?: () => void;
  children?: React.ReactNode;
}
