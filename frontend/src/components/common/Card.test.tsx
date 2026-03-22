/**
 * Tests for Card component
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/utils';
import { Card, StatCard } from './Card';

describe('Card', () => {
  describe('rendering', () => {
    it('should render children', () => {
      render(<Card>Card content</Card>);
      expect(screen.getByText('Card content')).toBeInTheDocument();
    });

    it('should render with test id', () => {
      render(<Card data-testid="test-card">Content</Card>);
      expect(screen.getByTestId('test-card')).toBeInTheDocument();
    });

    it('should render with id', () => {
      render(<Card id="my-card">Content</Card>);
      const card = screen.getByText('Content').closest('.card');
      expect(card).toHaveAttribute('id', 'my-card');
    });
  });

  describe('header', () => {
    it('should render title', () => {
      render(<Card title="Card Title">Content</Card>);
      expect(screen.getByText('Card Title')).toBeInTheDocument();
    });

    it('should render subtitle', () => {
      render(
        <Card title="Title" subtitle="Subtitle">
          Content
        </Card>
      );
      expect(screen.getByText('Subtitle')).toBeInTheDocument();
    });

    it('should render icon', () => {
      render(<Card icon={<span data-testid="card-icon">📊</span>}>Content</Card>);
      expect(screen.getByTestId('card-icon')).toBeInTheDocument();
    });

    it('should not render header if no title, subtitle, or icon', () => {
      render(<Card>Content</Card>);
      expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    });
  });

  describe('footer', () => {
    it('should render footer', () => {
      render(<Card footer={<span>Footer content</span>}>Content</Card>);
      expect(screen.getByText('Footer content')).toBeInTheDocument();
    });

    it('should not render footer if not provided', () => {
      render(<Card>Content</Card>);
      expect(screen.queryByText('Footer content')).not.toBeInTheDocument();
    });
  });

  describe('variants', () => {
    it('should render default variant', () => {
      render(<Card>Content</Card>);
      const card = screen.getByText('Content').closest('.card');
      expect(card).not.toHaveClass('border-primary');
    });

    it('should render primary variant', () => {
      render(<Card variant="primary">Content</Card>);
      const card = screen.getByText('Content').closest('.card');
      expect(card).toHaveClass('border-primary');
    });

    it('should render danger variant', () => {
      render(<Card variant="danger">Content</Card>);
      const card = screen.getByText('Content').closest('.card');
      expect(card).toHaveClass('border-danger');
    });
  });

  describe('custom className', () => {
    it('should merge custom className', () => {
      render(<Card className="custom-card">Content</Card>);
      const card = screen.getByText('Content').closest('.card');
      expect(card).toHaveClass('card');
      expect(card).toHaveClass('custom-card');
    });
  });
});

describe('StatCard', () => {
  describe('rendering', () => {
    it('should render label and value', () => {
      render(<StatCard label="Total Users" value={1000} />);
      expect(screen.getByText('Total Users')).toBeInTheDocument();
      expect(screen.getByText('1000')).toBeInTheDocument();
    });

    it('should render string value', () => {
      render(<StatCard label="Status" value="Active" />);
      expect(screen.getByText('Active')).toBeInTheDocument();
    });

    it('should render icon', () => {
      render(<StatCard label="Users" value={100} icon={<span data-testid="stat-icon">👥</span>} />);
      expect(screen.getByTestId('stat-icon')).toBeInTheDocument();
    });
  });

  describe('trend', () => {
    it('should render positive trend', () => {
      render(<StatCard label="Growth" value={100} trend={{ value: 15, isPositive: true }} />);
      expect(screen.getByText('15%')).toBeInTheDocument();
      const trendElement = screen.getByText('15%').closest('small');
      expect(trendElement).toHaveClass('text-success');
    });

    it('should render negative trend', () => {
      render(<StatCard label="Growth" value={100} trend={{ value: 10, isPositive: false }} />);
      expect(screen.getByText('10%')).toBeInTheDocument();
      const trendElement = screen.getByText('10%').closest('small');
      expect(trendElement).toHaveClass('text-danger');
    });

    it('should not render trend if not provided', () => {
      render(<StatCard label="Growth" value={100} />);
      expect(screen.queryByText('%')).not.toBeInTheDocument();
    });
  });

  describe('variants', () => {
    it('should render default variant', () => {
      render(<StatCard label="Stat" value={100} />);
      const card = screen.getByText('Stat').closest('.card');
      expect(card).toHaveClass('bg-light');
    });

    it('should render primary variant', () => {
      render(<StatCard label="Stat" value={100} variant="primary" />);
      const card = screen.getByText('Stat').closest('.card');
      expect(card).toHaveClass('bg-primary');
      expect(card).toHaveClass('text-white');
    });

    it('should render success variant', () => {
      render(<StatCard label="Stat" value={100} variant="success" />);
      const card = screen.getByText('Stat').closest('.card');
      expect(card).toHaveClass('bg-success');
    });
  });

  describe('custom className', () => {
    it('should merge custom className', () => {
      render(<StatCard label="Stat" value={100} className="custom-stat" />);
      const card = screen.getByText('Stat').closest('.card');
      expect(card).toHaveClass('stat-card');
      expect(card).toHaveClass('custom-stat');
    });
  });
});
