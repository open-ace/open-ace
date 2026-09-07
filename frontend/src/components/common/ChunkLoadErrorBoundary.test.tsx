import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChunkLoadErrorBoundary } from './ChunkLoadErrorBoundary';
import { isChunkLoadError } from './isChunkLoadError';

const consoleError = console.error;
const preventWindowError = (event: Event): void => event.preventDefault();

function Broken({ error }: { error: Error }): never {
  throw error;
}

describe('ChunkLoadErrorBoundary', () => {
  beforeEach(() => {
    console.error = vi.fn();
    window.addEventListener('error', preventWindowError);
  });

  afterEach(() => {
    window.removeEventListener('error', preventWindowError);
    console.error = consoleError;
  });

  it('conservatively recognizes browser chunk loading errors', () => {
    expect(
      isChunkLoadError(Object.assign(new Error('Loading chunk 42 failed'), { name: 'Error' }))
    ).toBe(true);
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module'))).toBe(
      true
    );
    expect(isChunkLoadError(new TypeError('Failed to fetch'))).toBe(false);
    expect(isChunkLoadError(new Error('API request failed'))).toBe(false);
  });

  it('shows a recoverable version message instead of a blank root', () => {
    render(
      <ChunkLoadErrorBoundary>
        <Broken error={new TypeError('Failed to fetch dynamically imported module')} />
      </ChunkLoadErrorBoundary>
    );

    // Updated to match new error diagnosis - "Resource Load Failed" for network errors
    expect(screen.getByRole('alert')).toHaveTextContent('Resource Load Failed');
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeVisible();
  });

  it('does not reload automatically and reloads only after an explicit click', () => {
    const reloadPage = vi.fn();
    render(
      <ChunkLoadErrorBoundary reloadPage={reloadPage}>
        <Broken
          error={Object.assign(new Error('Loading chunk autonomous failed'), {
            name: 'ChunkLoadError',
          })}
        />
      </ChunkLoadErrorBoundary>
    );

    expect(reloadPage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Reload page' }));
    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  it('uses a generic recovery message for unrelated render failures', () => {
    render(
      <ChunkLoadErrorBoundary reloadPage={vi.fn()}>
        <Broken error={new Error('private implementation detail')} />
      </ChunkLoadErrorBoundary>
    );

    // Updated to match new error diagnosis - "Page Render Error" for runtime errors
    expect(screen.getByRole('alert')).toHaveTextContent('Page Render Error');
    expect(screen.getByRole('alert')).not.toHaveTextContent('private implementation detail');
    // Contact Support button should be visible for runtime errors
    expect(screen.getByRole('button', { name: /Contact Support/i })).toBeVisible();
  });
});
