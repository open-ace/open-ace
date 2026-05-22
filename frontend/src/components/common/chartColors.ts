const COLORS = {
  primary: 'rgba(37, 99, 235, 1)',
  primaryLight: 'rgba(37, 99, 235, 0.2)',
  success: 'rgba(34, 197, 94, 1)',
  successLight: 'rgba(34, 197, 94, 0.2)',
  warning: 'rgba(245, 158, 11, 1)',
  warningLight: 'rgba(245, 158, 11, 0.2)',
  danger: 'rgba(239, 68, 68, 1)',
  dangerLight: 'rgba(239, 68, 68, 0.2)',
  info: 'rgba(59, 130, 246, 1)',
  infoLight: 'rgba(59, 130, 246, 0.2)',
  purple: 'rgba(139, 92, 246, 1)',
  purpleLight: 'rgba(139, 92, 246, 0.2)',
  cyan: 'rgba(6, 182, 212, 1)',
  cyanLight: 'rgba(6, 182, 212, 0.2)',
};

export { COLORS };

export const TOOL_COLORS: Record<string, { border: string; background: string; solid: string }> = {
  openclaw: {
    border: 'rgba(255, 99, 132, 1)',
    background: 'rgba(255, 99, 132, 0.2)',
    solid: 'rgba(255, 99, 132, 0.8)',
  },
  claude: {
    border: 'rgba(75, 192, 192, 1)',
    background: 'rgba(75, 192, 192, 0.2)',
    solid: 'rgba(75, 192, 192, 0.8)',
  },
  qwen: {
    border: 'rgba(54, 162, 235, 1)',
    background: 'rgba(54, 162, 235, 0.2)',
    solid: 'rgba(54, 162, 235, 0.8)',
  },
};

export const getToolColor = (tool: string, index: number) => {
  const toolColors = TOOL_COLORS[tool.toLowerCase()];
  if (toolColors) return toolColors;

  const solidColors = [
    'rgba(37, 99, 235, 0.8)',
    'rgba(34, 197, 94, 0.8)',
    'rgba(245, 158, 11, 0.8)',
    'rgba(239, 68, 68, 0.8)',
    'rgba(139, 92, 246, 0.8)',
    'rgba(6, 182, 212, 0.8)',
  ];
  return {
    border: [
      COLORS.primary,
      COLORS.success,
      COLORS.warning,
      COLORS.danger,
      COLORS.purple,
      COLORS.cyan,
    ][index % 6],
    background: [
      COLORS.primaryLight,
      COLORS.successLight,
      COLORS.warningLight,
      COLORS.dangerLight,
      COLORS.purpleLight,
      COLORS.cyanLight,
    ][index % 6],
    solid: solidColors[index % 6],
  };
};
