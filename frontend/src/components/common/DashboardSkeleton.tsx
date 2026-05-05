/**
 * DashboardSkeleton Component - Skeleton loading for Dashboard page
 *
 * Provides a visual placeholder that mimics the Dashboard layout structure
 * while data is being loaded, improving perceived performance.
 */

import React from 'react';
import { Skeleton, SkeletonCard, SkeletonTable, SkeletonList } from './Skeleton';

/**
 * DashboardSkeleton - Full dashboard loading placeholder
 */
export const DashboardSkeleton: React.FC = () => {
  return (
    <div className="dashboard">
      {/* Header */}
      <div className="dashboard-header d-flex justify-content-between align-items-center mb-4">
        <Skeleton variant="text" height={32} width={200} />
        <div className="d-flex gap-2">
          <Skeleton variant="rounded" height={32} width={120} />
          <Skeleton variant="rounded" height={32} width={120} />
        </div>
      </div>

      {/* Today's Usage Section */}
      <section className="dashboard-section mb-4">
        <Skeleton variant="text" height={24} width={150} className="mb-3" />
        <div className="row g-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="col-md-4">
              <TodayCardSkeleton />
            </div>
          ))}
        </div>
      </section>

      {/* Total Overview Section */}
      <section className="dashboard-section mb-4">
        <Skeleton variant="text" height={24} width={150} className="mb-3" />
        <div className="row g-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="col-md-4">
              <SummaryCardSkeleton />
            </div>
          ))}
        </div>
      </section>

      {/* Charts Section */}
      <section className="dashboard-section mb-4">
        <div className="row">
          <div className="col-md-8 mb-4">
            <ChartCardSkeleton titleWidth={150} height={300} />
          </div>
          <div className="col-md-4 mb-4">
            <ChartCardSkeleton titleWidth={120} height={300} />
          </div>
        </div>
      </section>

      {/* Tools Info Table */}
      <section className="dashboard-section">
        <TableCardSkeleton />
      </section>
    </div>
  );
};

/**
 * TodayCardSkeleton - Skeleton for Today's Usage card
 */
const TodayCardSkeleton: React.FC = () => {
  return (
    <div className="card usage-card text-white bg-secondary">
      <div className="card-body">
        <Skeleton variant="text" height={20} width="40%" className="mb-3 bg-white/20" />
        <Skeleton variant="text" height={36} width="60%" className="mb-3 bg-white/20" />
        <Skeleton variant="text" height={16} width="50%" className="mb-1 bg-white/20" />
        <Skeleton variant="text" height={16} width="40%" className="bg-white/20" />
      </div>
    </div>
  );
};

/**
 * SummaryCardSkeleton - Skeleton for Summary card
 */
const SummaryCardSkeleton: React.FC = () => {
  return (
    <div className="card usage-card text-white bg-secondary">
      <div className="card-body">
        <Skeleton variant="text" height={24} width="30%" className="mb-2 bg-white/20" />
        <Skeleton variant="text" height={20} width="80%" className="mb-1 bg-white/20" />
        <Skeleton variant="text" height={16} width="60%" className="mb-1 bg-white/20" />
        <Skeleton variant="text" height={16} width="50%" className="mb-1 bg-white/20" />
        <Skeleton variant="text" height={14} width="70%" className="bg-white/20" />
      </div>
    </div>
  );
};

/**
 * ChartCardSkeleton - Skeleton for chart card
 */
interface ChartCardSkeletonProps {
  titleWidth?: number | string;
  height?: number;
}

const ChartCardSkeleton: React.FC<ChartCardSkeletonProps> = ({ titleWidth = 150, height = 300 }) => {
  return (
    <div className="card">
      <div className="card-header d-flex justify-content-between align-items-center mb-3 pb-2 border-bottom">
        <Skeleton variant="text" height={20} width={titleWidth} />
      </div>
      <div className="card-body">
        <Skeleton variant="rectangular" height={height} className="w-100" />
      </div>
    </div>
  );
};

/**
 * TableCardSkeleton - Skeleton for table card
 */
const TableCardSkeleton: React.FC = () => {
  return (
    <div className="card">
      <div className="card-header d-flex justify-content-between align-items-center mb-3 pb-2 border-bottom">
        <Skeleton variant="text" height={20} width={120} />
      </div>
      <div className="card-body">
        <SkeletonTable rows={4} columns={8} />
      </div>
    </div>
  );
};

/**
 * PageSkeleton - Generic page loading skeleton
 * Used for Suspense fallback in route-level lazy loading
 */
export const PageSkeleton: React.FC = () => {
  return (
    <div className="page-skeleton d-flex flex-column min-vh-100">
      {/* Header placeholder */}
      <div className="skeleton-header d-flex justify-content-between align-items-center p-3 border-bottom">
        <Skeleton variant="text" height={24} width={180} />
        <div className="d-flex gap-2">
          <Skeleton variant="circular" width={32} height={32} />
          <Skeleton variant="circular" width={32} height={32} />
        </div>
      </div>

      {/* Main content placeholder */}
      <div className="skeleton-content flex-grow-1 p-4">
        <div className="row">
          <div className="col-12">
            <SkeletonCard hasHeader lines={4} />
          </div>
        </div>
        <div className="row mt-4">
          <div className="col-md-6">
            <SkeletonCard hasHeader lines={3} />
          </div>
          <div className="col-md-6">
            <SkeletonCard hasHeader lines={3} />
          </div>
        </div>
      </div>
    </div>
  );
};

/**
 * ManagePageSkeleton - Skeleton for Manage layout pages
 */
export const ManagePageSkeleton: React.FC = () => {
  return (
    <div className="manage-layout d-flex min-vh-100">
      {/* Sidebar placeholder */}
      <div className="skeleton-sidebar bg-dark" style={{ width: '250px' }}>
        <div className="p-3">
          <Skeleton variant="text" height={24} width="80%" className="bg-white/20 mb-4" />
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <Skeleton
              key={i}
              variant="rounded"
              height={40}
              className="w-100 mb-2 bg-white/10"
            />
          ))}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-grow-1">
        <PageSkeleton />
      </div>
    </div>
  );
};

/**
 * WorkPageSkeleton - Skeleton for Work layout pages
 */
export const WorkPageSkeleton: React.FC = () => {
  return (
    <div className="work-layout d-flex min-vh-100">
      {/* Sidebar placeholder */}
      <div className="skeleton-sidebar bg-dark" style={{ width: '60px' }}>
        <div className="p-2 d-flex flex-column gap-2">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} variant="circular" width={40} height={40} className="bg-white/10" />
          ))}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-grow-1 p-4">
        <div className="row g-3">
          <div className="col-md-8">
            <SkeletonCard hasHeader lines={5} />
          </div>
          <div className="col-md-4">
            <SkeletonList items={4} hasAvatar />
          </div>
        </div>
      </div>
    </div>
  );
};
