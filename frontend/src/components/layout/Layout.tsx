/**
 * Layout Component - Main application layout
 */

import React from 'react';
import { cn } from '@/utils';
import { useSidebarCollapsed } from '@/store';
import { Sidebar } from './Sidebar';
import { Header } from './Header';

interface LayoutProps {
  children: React.ReactNode;
  activeSection: string;
  title?: string;
  onNavigate: (section: string) => void;
}

export const Layout: React.FC<LayoutProps> = ({ children, activeSection, onNavigate }) => {
  const collapsed = useSidebarCollapsed();

  return (
    <div className={cn('app-layout', collapsed && 'sidebar-collapsed')}>
      <Sidebar activeSection={activeSection} onNavigate={onNavigate} />
      <div className="main-content d-flex flex-column">
        <Header />
        <main className="content-area flex-grow-1 p-3">{children}</main>
      </div>
    </div>
  );
};
