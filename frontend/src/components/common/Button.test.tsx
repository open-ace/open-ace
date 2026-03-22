/**
 * Tests for Button component
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@/test/utils';
import { Button } from './Button';

describe('Button', () => {
  describe('rendering', () => {
    it('should render children', () => {
      render(<Button>Click me</Button>);
      expect(screen.getByRole('button')).toHaveTextContent('Click me');
    });

    it('should render with icon', () => {
      render(<Button icon={<span data-testid="icon">🔍</span>}>Search</Button>);
      expect(screen.getByTestId('icon')).toBeInTheDocument();
      expect(screen.getByRole('button')).toHaveTextContent('Search');
    });

    it('should render with test id', () => {
      render(<Button data-testid="submit-btn">Submit</Button>);
      expect(screen.getByTestId('submit-btn')).toBeInTheDocument();
    });

    it('should render with id', () => {
      render(<Button id="my-button">Button</Button>);
      expect(screen.getByRole('button')).toHaveAttribute('id', 'my-button');
    });
  });

  describe('variants', () => {
    it('should render primary variant by default', () => {
      render(<Button>Primary</Button>);
      expect(screen.getByRole('button')).toHaveClass('btn-primary');
    });

    it('should render secondary variant', () => {
      render(<Button variant="secondary">Secondary</Button>);
      expect(screen.getByRole('button')).toHaveClass('btn-secondary');
    });

    it('should render danger variant', () => {
      render(<Button variant="danger">Delete</Button>);
      expect(screen.getByRole('button')).toHaveClass('btn-danger');
    });

    it('should render success variant', () => {
      render(<Button variant="success">Save</Button>);
      expect(screen.getByRole('button')).toHaveClass('btn-success');
    });
  });

  describe('sizes', () => {
    it('should render medium size by default', () => {
      render(<Button>Medium</Button>);
      expect(screen.getByRole('button')).not.toHaveClass('btn-sm');
      expect(screen.getByRole('button')).not.toHaveClass('btn-lg');
    });

    it('should render small size', () => {
      render(<Button size="sm">Small</Button>);
      expect(screen.getByRole('button')).toHaveClass('btn-sm');
    });

    it('should render large size', () => {
      render(<Button size="lg">Large</Button>);
      expect(screen.getByRole('button')).toHaveClass('btn-lg');
    });
  });

  describe('states', () => {
    it('should be disabled when disabled prop is true', () => {
      render(<Button disabled>Disabled</Button>);
      expect(screen.getByRole('button')).toBeDisabled();
    });

    it('should be disabled when loading', () => {
      render(<Button loading>Loading</Button>);
      expect(screen.getByRole('button')).toBeDisabled();
    });

    it('should show spinner when loading', () => {
      render(<Button loading>Loading</Button>);
      // Spinner has aria-hidden, so we check for the class instead
      const spinner = document.querySelector('.spinner-border');
      expect(spinner).toBeInTheDocument();
    });

    it('should show children when loading', () => {
      render(<Button loading>Saving...</Button>);
      expect(screen.getByRole('button')).toHaveTextContent('Saving...');
    });
  });

  describe('full width', () => {
    it('should have w-100 class when fullWidth is true', () => {
      render(<Button fullWidth>Full Width</Button>);
      expect(screen.getByRole('button')).toHaveClass('w-100');
    });

    it('should not have w-100 class by default', () => {
      render(<Button>Normal Width</Button>);
      expect(screen.getByRole('button')).not.toHaveClass('w-100');
    });
  });

  describe('interactions', () => {
    it('should call onClick when clicked', () => {
      const handleClick = vi.fn();
      render(<Button onClick={handleClick}>Click me</Button>);

      fireEvent.click(screen.getByRole('button'));

      expect(handleClick).toHaveBeenCalledTimes(1);
    });

    it('should not call onClick when disabled', () => {
      const handleClick = vi.fn();
      render(
        <Button disabled onClick={handleClick}>
          Disabled
        </Button>
      );

      fireEvent.click(screen.getByRole('button'));

      expect(handleClick).not.toHaveBeenCalled();
    });

    it('should not call onClick when loading', () => {
      const handleClick = vi.fn();
      render(
        <Button loading onClick={handleClick}>
          Loading
        </Button>
      );

      fireEvent.click(screen.getByRole('button'));

      expect(handleClick).not.toHaveBeenCalled();
    });
  });

  describe('type attribute', () => {
    it('should have type="button" by default', () => {
      render(<Button>Button</Button>);
      expect(screen.getByRole('button')).toHaveAttribute('type', 'button');
    });

    it('should have type="submit" when specified', () => {
      render(<Button type="submit">Submit</Button>);
      expect(screen.getByRole('button')).toHaveAttribute('type', 'submit');
    });
  });

  describe('custom className', () => {
    it('should merge custom className', () => {
      render(<Button className="custom-class">Custom</Button>);
      const button = screen.getByRole('button');
      expect(button).toHaveClass('btn');
      expect(button).toHaveClass('btn-primary');
      expect(button).toHaveClass('custom-class');
    });
  });
});
