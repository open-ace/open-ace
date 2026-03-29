/**
 * Tabs Component - Tabbed interface with animations
 */

import React, { useState, createContext, useContext } from 'react';
import { cn } from '@/utils';

interface TabsContextValue {
  activeTab: string;
  setActiveTab: (id: string) => void;
}

const TabsContext = createContext<TabsContextValue | null>(null);

const useTabsContext = () => {
  const context = useContext(TabsContext);
  if (!context) {
    throw new Error('Tab components must be used within a Tabs component');
  }
  return context;
};

interface TabsProps {
  defaultTab?: string;
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
  children: React.ReactNode;
  className?: string;
}

export const Tabs: React.FC<TabsProps> = ({
  defaultTab,
  activeTab: controlledActiveTab,
  onTabChange,
  children,
  className,
}) => {
  const [internalActiveTab, setInternalActiveTab] = useState(defaultTab ?? '');

  const activeTab = controlledActiveTab ?? internalActiveTab;
  const setActiveTab = (id: string) => {
    if (!controlledActiveTab) {
      setInternalActiveTab(id);
    }
    onTabChange?.(id);
  };

  return (
    <TabsContext.Provider value={{ activeTab, setActiveTab }}>
      <div className={cn('tabs-container', className)}>{children}</div>
    </TabsContext.Provider>
  );
};

interface TabListProps {
  children: React.ReactNode;
  className?: string;
}

export const TabList: React.FC<TabListProps> = ({ children, className }) => {
  return (
    <ul className={cn('nav nav-tabs', className)} role="tablist">
      {children}
    </ul>
  );
};

interface TabProps {
  id: string;
  children: React.ReactNode;
  disabled?: boolean;
  icon?: React.ReactNode;
  className?: string;
}

export const Tab: React.FC<TabProps> = ({ id, children, disabled = false, icon, className }) => {
  const { activeTab, setActiveTab } = useTabsContext();
  const isActive = activeTab === id;

  return (
    <li className="nav-item" role="presentation">
      <button
        className={cn('nav-link', isActive && 'active', disabled && 'disabled', className)}
        role="tab"
        aria-selected={isActive}
        aria-controls={`tabpanel-${id}`}
        disabled={disabled}
        onClick={() => !disabled && setActiveTab(id)}
      >
        {icon && <span className="me-2">{icon}</span>}
        {children}
      </button>
    </li>
  );
};

interface TabPanelsProps {
  children: React.ReactNode;
  className?: string;
}

export const TabPanels: React.FC<TabPanelsProps> = ({ children, className }) => {
  return <div className={cn('tab-content', className)}>{children}</div>;
};

interface TabPanelProps {
  id: string;
  children: React.ReactNode;
  className?: string;
  keepMounted?: boolean;
}

export const TabPanel: React.FC<TabPanelProps> = ({
  id,
  children,
  className,
  keepMounted = false,
}) => {
  const { activeTab } = useTabsContext();
  const isActive = activeTab === id;

  if (!isActive && !keepMounted) {
    return null;
  }

  return (
    <div
      id={`tabpanel-${id}`}
      className={cn('tab-pane fade', isActive && 'show active animate-fade-in', className)}
      role="tabpanel"
      aria-labelledby={`tab-${id}`}
      hidden={!isActive}
    >
      {children}
    </div>
  );
};

/**
 * SimpleTabs - Pre-built simple tabs component
 */
interface SimpleTabsProps {
  tabs: Array<{
    id: string;
    label: string;
    icon?: React.ReactNode;
    content: React.ReactNode;
  }>;
  defaultTab?: string;
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
  className?: string;
}

export const SimpleTabs: React.FC<SimpleTabsProps> = ({
  tabs,
  defaultTab,
  activeTab,
  onTabChange,
  className,
}) => {
  return (
    <Tabs defaultTab={defaultTab ?? tabs[0]?.id} activeTab={activeTab} onTabChange={onTabChange}>
      <TabList className={cn('mb-3', className)}>
        {tabs.map((tab) => (
          <Tab key={tab.id} id={tab.id} icon={tab.icon}>
            {tab.label}
          </Tab>
        ))}
      </TabList>
      <TabPanels>
        {tabs.map((tab) => (
          <TabPanel key={tab.id} id={tab.id}>
            {tab.content}
          </TabPanel>
        ))}
      </TabPanels>
    </Tabs>
  );
};
