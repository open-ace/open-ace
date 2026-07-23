import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Modal } from './Modal';

describe('Modal', () => {
  it('places header actions beside the title without adding a body toolbar row', () => {
    const onClose = vi.fn();
    render(
      <Modal
        isOpen
        onClose={onClose}
        title="Final plan"
        headerActions={<button type="button">Enter fullscreen</button>}
      >
        <p>Document body</p>
      </Modal>
    );

    const title = screen.getByRole('heading', { name: 'Final plan' });
    const action = screen.getByRole('button', { name: 'Enter fullscreen' });
    expect(title.parentElement).toBe(action.parentElement?.parentElement);
    expect(screen.getByText('Document body').closest('.modal-body')).not.toContainElement(action);

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
