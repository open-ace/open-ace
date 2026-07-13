/**
 * ModelGatewayConfig Component Tests
 *
 * Tests cover:
 * - Three-level status display: disabled / enabled no config / enabled with config
 * - Loading and error states
 * - Save, test, and delete interactions
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// Mock language hook
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

// Mock i18n
vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      gatewayDisabled: 'Gateway Disabled',
      gatewayEnabledNoConfig: 'Gateway Enabled (No Config)',
      gatewayEnabled: 'Gateway Enabled',
      gatewayEnableInstructions: 'Enable the gateway in config.json to use this feature',
      modelGatewayConfiguration: 'Model Gateway Configuration',
      modelGatewayDesc: 'Configure your LiteLLM-compatible model gateway',
      gatewayBaseUrl: 'Base URL',
      gatewayApiKey: 'API Key',
      modelPrefixMode: 'Model Prefix Mode',
      modelPrefix: 'Model Prefix',
      save: 'Save',
      testConnection: 'Test Connection',
      delete: 'Delete',
      loading: 'Loading...',
    };
    return translations[key] || key;
  },
}));

// Mock common components
vi.mock('@/components/common', () => ({
  Card: ({ children }: { children: React.ReactNode }) => <div data-testid="card">{children}</div>,
  Button: ({
    children,
    onClick,
    disabled,
    variant,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    variant?: string;
  }) => (
    <button onClick={onClick} disabled={disabled} data-variant={variant}>
      {children}
    </button>
  ),
  TextInput: ({
    value,
    onChange,
    placeholder,
    type,
  }: {
    value: string;
    onChange: (v: string) => void;
    placeholder?: string;
    type?: string;
  }) => (
    <input
      type={type || 'text'}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      data-testid={placeholder}
    />
  ),
  Loading: () => <div data-testid="loading">Loading...</div>,
  Error: ({ message, onRetry }: { message: string; onRetry?: () => void }) => (
    <div data-testid="error">
      {message}
      {onRetry && <button onClick={onRetry}>Retry</button>}
    </div>
  ),
  Badge: ({ children, variant }: { children: React.ReactNode; variant: string }) => (
    <span data-testid="badge" data-variant={variant}>
      {children}
    </span>
  ),
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
  }),
  useConfirm: () => vi.fn().mockResolvedValue(true),
}));

// Mock API - must use vi.fn() inside the mock factory
vi.mock('@/api/modelGateway', () => ({
  modelGatewayApi: {
    getConfig: vi.fn(),
    saveConfig: vi.fn(),
    testConnection: vi.fn(),
    deleteConfig: vi.fn(),
  },
}));

import { ModelGatewayConfig } from './ModelGatewayConfig';
import { modelGatewayApi } from '@/api/modelGateway';

describe('ModelGatewayConfig', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Status Display', () => {
    it('shows disabled status when gateway is not enabled', async () => {
      vi.mocked(modelGatewayApi.getConfig).mockResolvedValue({
        enabled: false,
        data: null,
      });

      render(<ModelGatewayConfig />);

      await waitFor(() => {
        expect(screen.getByText('Gateway Disabled')).toBeInTheDocument();
      });

      // Should show enable instructions
      expect(
        screen.getByText('Enable the gateway in config.json to use this feature')
      ).toBeInTheDocument();
    });

    it('shows enabled but no config status when gateway is enabled but not configured', async () => {
      vi.mocked(modelGatewayApi.getConfig).mockResolvedValue({
        enabled: true,
        data: null,
      });

      render(<ModelGatewayConfig />);

      await waitFor(() => {
        expect(screen.getByText('Gateway Enabled (No Config)')).toBeInTheDocument();
      });

      // Should NOT show enable instructions
      expect(
        screen.queryByText('Enable the gateway in config.json to use this feature')
      ).not.toBeInTheDocument();
    });

    it('shows enabled status when gateway is enabled and configured', async () => {
      vi.mocked(modelGatewayApi.getConfig).mockResolvedValue({
        enabled: true,
        data: {
          base_url: 'https://gateway.example.com',
          model_prefix_mode: false,
          api_key_masked: 'sk-***',
        },
      });

      render(<ModelGatewayConfig />);

      await waitFor(() => {
        expect(screen.getByText('Gateway Enabled')).toBeInTheDocument();
      });

      // Should show badge with success variant
      const badges = screen.getAllByTestId('badge');
      expect(badges[0]).toHaveAttribute('data-variant', 'success');
    });
  });

  describe('Loading and Error States', () => {
    it('shows loading state initially', () => {
      vi.mocked(modelGatewayApi.getConfig).mockImplementation(() => new Promise(() => {})); // Never resolves

      render(<ModelGatewayConfig />);
      expect(screen.getByTestId('loading')).toBeInTheDocument();
    });

    it('shows error state when config fetch fails', async () => {
      vi.mocked(modelGatewayApi.getConfig).mockRejectedValue(new Error('Network error'));

      render(<ModelGatewayConfig />);

      await waitFor(() => {
        expect(screen.getByTestId('error')).toBeInTheDocument();
        expect(screen.getByText('Network error')).toBeInTheDocument();
      });
    });
  });

  describe('Form Interactions', () => {
    it('populates form with existing config', async () => {
      vi.mocked(modelGatewayApi.getConfig).mockResolvedValue({
        enabled: true,
        data: {
          base_url: 'https://gateway.example.com',
          model_prefix_mode: true,
          model_prefix: 'gpt-4',
          api_key_masked: 'sk-***',
        },
      });

      render(<ModelGatewayConfig />);

      await waitFor(() => {
        expect(screen.getByText('Gateway Enabled')).toBeInTheDocument();
      });

      // Check form is populated (base_url)
      const urlInput = screen.getByDisplayValue('https://gateway.example.com');
      expect(urlInput).toBeInTheDocument();
    });

    it('calls save API when save button is clicked', async () => {
      vi.mocked(modelGatewayApi.getConfig).mockResolvedValue({
        enabled: true,
        data: null,
      });
      vi.mocked(modelGatewayApi.saveConfig).mockResolvedValue(undefined);

      render(<ModelGatewayConfig />);

      await waitFor(() => {
        expect(screen.getByText('Gateway Enabled (No Config)')).toBeInTheDocument();
      });

      // Fill in base URL
      const urlInput = screen.getByTestId('https://litellm.example.com/v1');
      fireEvent.change(urlInput, { target: { value: 'https://new.example.com' } });

      // Click save
      const saveButton = screen.getByRole('button', { name: 'Save' });
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(modelGatewayApi.saveConfig).toHaveBeenCalledWith({
          base_url: 'https://new.example.com',
          api_key: undefined,
          model_prefix_mode: false,
          model_prefix: null,
        });
      });
    });

    it('calls test API when test button is clicked', async () => {
      vi.mocked(modelGatewayApi.getConfig).mockResolvedValue({
        enabled: true,
        data: null,
      });
      vi.mocked(modelGatewayApi.testConnection).mockResolvedValue({
        ok: true,
        message: 'Connection successful',
      });

      render(<ModelGatewayConfig />);

      await waitFor(() => {
        expect(screen.getByText('Gateway Enabled (No Config)')).toBeInTheDocument();
      });

      // Fill in base URL
      const urlInput = screen.getByTestId('https://litellm.example.com/v1');
      fireEvent.change(urlInput, { target: { value: 'https://gateway.example.com' } });

      // Click test
      const testButton = screen.getByRole('button', { name: 'Test Connection' });
      fireEvent.click(testButton);

      await waitFor(() => {
        expect(modelGatewayApi.testConnection).toHaveBeenCalled();
      });
    });

    it('shows delete button only when config exists', async () => {
      vi.mocked(modelGatewayApi.getConfig).mockResolvedValue({
        enabled: true,
        data: {
          base_url: 'https://gateway.example.com',
          model_prefix_mode: false,
        },
      });

      render(<ModelGatewayConfig />);

      await waitFor(() => {
        expect(screen.getByText('Gateway Enabled')).toBeInTheDocument();
      });

      // Delete button should be visible
      expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
    });

    it('hides delete button when no config exists', async () => {
      vi.mocked(modelGatewayApi.getConfig).mockResolvedValue({
        enabled: true,
        data: null,
      });

      render(<ModelGatewayConfig />);

      await waitFor(() => {
        expect(screen.getByText('Gateway Enabled (No Config)')).toBeInTheDocument();
      });

      // Delete button should NOT be visible
      expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
    });
  });
});
