/**
 * Layout Component - Main application layout
 */

import React, { useEffect } from 'react';
import { cn } from '@/utils';
import { useSidebarCollapsed, useMobileSidebarOpen } from '@/store';
import { useAppStore } from '@/store';
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
  const mobileOpen = useMobileSidebarOpen();

  useEffect(() => {
    document.body.classList.toggle('sidebar-mobile-open', mobileOpen);
    return () => document.body.classList.remove('sidebar-mobile-open');
  }, [mobileOpen]);

  const closeMobileSidebar = () => {
    useAppStore.getState().setMobileSidebarOpen(false);
  };

  return (
    <div className={cn('app-layout', collapsed && 'sidebar-collapsed')}>
      <Sidebar activeSection={activeSection} onNavigate={onNavigate} mobileOpen={mobileOpen} />
      {mobileOpen && <div className="sidebar-overlay" onClick={closeMobileSidebar} />}
      <div className="main-content d-flex flex-column">
        <Header />
        <main className="content-area flex-grow-1 p-3">{children}</main>
      </div>
    </div>
  );
};
