/**
 * Common Components - Export all common UI components
 */

// Core components
export { Button } from './Button';
export { Card, StatCard } from './Card';
export { Loading, LoadingOverlay, Skeleton, SkeletonCard } from './Loading';
export { Error, EmptyState } from './Error';
export { Select, createOptions } from './Select';
export { SearchableSelect } from './SearchableSelect';

// New Phase 5 components
export { Modal, ConfirmModal } from './Modal';
export { ToastContainer, useToast } from './Toast';
export type { ToastData, ToastType } from './Toast';
export { Tooltip } from './Tooltip';
export type { TooltipPlacement } from './Tooltip';
export { Badge, StatusBadge, CountBadge } from './Badge';
export type { BadgeVariant } from './Badge';
export { Tabs, TabList, Tab, TabPanels, TabPanel, SimpleTabs } from './Tabs';
export { Progress, CircularProgress, StepsProgress } from './Progress';
export { SkeletonText, SkeletonTable, SkeletonList } from './Skeleton';
export { TextInput, Textarea, Checkbox, RadioGroup, Switch } from './Input';
export { Avatar, AvatarGroup } from './Avatar';
export { Dropdown, SplitButton } from './Dropdown';
export { Divider } from './Divider';

// Mode Switcher - Dual-track system
export { ModeSwitcher } from './ModeSwitcher';

// Chart components - lazy loading for performance optimization
// Chart.js (~60KB) is loaded on demand when charts are rendered
export {
  LazyLineChart as LineChart,
  LazyBarChart as BarChart,
  LazyPieChart as PieChart,
  LazyDoughnutChart as DoughnutChart,
  LazyTokenTrendChart as TokenTrendChart,
  LazyToolUsageChart as ToolUsageChart,
  LazyTokenDistributionChart as TokenDistributionChart,
} from './LazyCharts';
export type {
  LineChartProps,
  BarChartProps,
  PieChartProps,
  DoughnutChartProps,
  TokenTrendChartProps,
  ToolUsageChartProps,
  TokenDistributionChartProps,
} from './LazyCharts';
