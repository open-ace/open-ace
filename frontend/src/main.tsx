/**
 * Open ACE Frontend - Main Entry Point
 *
 * Phase 4 Optimization: React-based frontend with TypeScript
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';

// Mount the React application
const rootElement = document.getElementById('root');

if (rootElement) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>
  );
} else {
  console.error('[Open ACE] Root element not found');
}
