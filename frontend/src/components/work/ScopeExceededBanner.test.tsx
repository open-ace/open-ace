import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { render } from '@/test/utils';
import { ScopeExceededBanner } from './ScopeExceededBanner';

describe('ScopeExceededBanner', () => {
  const baseProps = {
    fileCount: 143,
    currentLimit: 60,
    language: 'en' as const,
    isPending: false,
    onRetryWithLimit: vi.fn(),
    onPlainRetry: vi.fn(),
  };

  it('offers smart presets anchored to the file count, smallest marked suggested', () => {
    render(<ScopeExceededBanner {...baseProps} />);
    // 143 → roundUp50=150 (suggested), roundUp100=200, roundUp(286,50)=300.
    expect(screen.getByText(/150/)).toBeInTheDocument();
    expect(screen.getByText(/200/)).toBeInTheDocument();
    expect(screen.getByText(/300/)).toBeInTheDocument();
    // Only the smallest preset is badged suggested.
    expect(screen.getByText(/150.*suggested/i)).toBeInTheDocument();
    expect(screen.queryByText(/200.*suggested/i)).not.toBeInTheDocument();
  });

  it('retries with the chosen preset limit when its radio + retry are clicked', () => {
    const onRetryWithLimit = vi.fn();
    render(<ScopeExceededBanner {...baseProps} onRetryWithLimit={onRetryWithLimit} />);
    fireEvent.click(screen.getByLabelText(/200/));
    fireEvent.click(screen.getByText(/Retry with new cap/i));
    expect(onRetryWithLimit).toHaveBeenCalledWith(200);
  });

  it('defaults to the suggested preset so one click retries with 150', () => {
    const onRetryWithLimit = vi.fn();
    render(<ScopeExceededBanner {...baseProps} onRetryWithLimit={onRetryWithLimit} />);
    fireEvent.click(screen.getByText(/Retry with new cap/i));
    expect(onRetryWithLimit).toHaveBeenCalledWith(150);
  });

  it('supports a custom limit via the custom radio + number input', () => {
    const onRetryWithLimit = vi.fn();
    render(<ScopeExceededBanner {...baseProps} onRetryWithLimit={onRetryWithLimit} />);
    fireEvent.click(screen.getByLabelText(/Custom/i));
    const input = screen.getByRole('spinbutton', { name: /custom/i });
    fireEvent.change(input, { target: { value: '250' } });
    fireEvent.click(screen.getByText(/Retry with new cap/i));
    expect(onRetryWithLimit).toHaveBeenCalledWith(250);
  });

  it('disables the raise-and-retry button until the custom input is a positive integer', () => {
    render(<ScopeExceededBanner {...baseProps} onRetryWithLimit={vi.fn()} />);
    fireEvent.click(screen.getByLabelText(/Custom/i));
    // Empty custom → button disabled.
    expect(screen.getByText(/Retry with new cap/i)).toBeDisabled();
    // Non-positive → still disabled.
    fireEvent.change(screen.getByRole('spinbutton', { name: /custom/i }), {
      target: { value: '0' },
    });
    expect(screen.getByText(/Retry with new cap/i)).toBeDisabled();
  });

  it('plain retry does not pass an override', () => {
    const onPlainRetry = vi.fn();
    render(<ScopeExceededBanner {...baseProps} onPlainRetry={onPlainRetry} />);
    fireEvent.click(screen.getByText(/Plain retry/i));
    expect(onPlainRetry).toHaveBeenCalledTimes(1);
  });
});
