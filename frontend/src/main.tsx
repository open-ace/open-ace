/**
 * Open ACE Frontend - Main Entry Point
 *
 * Phase 4 Optimization: React-based frontend with TypeScript
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';

// Import Bootstrap and Bootstrap Icons locally (no external CDN)
import 'bootstrap/dist/css/bootstrap.min.css';
import 'bootstrap-icons/font/bootstrap-icons.css';
import 'bootstrap/dist/js/bootstrap.bundle.min.js';

// Import custom styles AFTER Bootstrap to override defaults
import '@/styles/main.css';

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
